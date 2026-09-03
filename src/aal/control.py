from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation

from .store import Store


class ApprovalError(PermissionError): pass
class GenerationConflict(RuntimeError): pass
class OverlapError(RuntimeError): pass
class BoundsError(ValueError): pass


@dataclass(frozen=True)
class ActionRequest:
    case_id: str
    action: str
    amount: str
    currency: str
    idempotency_key: str
    expected_generation: int
    authority: str


@dataclass(frozen=True)
class ActionResult:
    status: str
    effect_id: str | None
    readback: dict | None


class BoundedLedger:
    ACTIONS = {"reverse_duplicate", "record_timing_adjustment"}
    MAX_AMOUNT = Decimal("1000.00")

    def __init__(self, store: Store):
        self.store = store

    def _authorize(self, request: ActionRequest):
        case = self.store.case(request.case_id)
        if not case:
            raise KeyError(request.case_id)
        if case["generation"] != request.expected_generation:
            raise GenerationConflict(f"expected {request.expected_generation}, found {case['generation']}")
        if request.action not in self.ACTIONS:
            raise BoundsError("action outside bounded allowlist")
        try:
            amount = abs(Decimal(request.amount))
        except InvalidOperation as exc:
            raise BoundsError("invalid amount") from exc
        if amount > self.MAX_AMOUNT or request.currency != "USD":
            raise BoundsError("amount or currency outside bounds")
        if request.authority == "proposal":
            approval = self.store.approval(request.case_id)
            if not approval or approval["decision"] != "approved" or not approval["actor"]:
                raise ApprovalError("attributable human approval required")
        elif request.authority != "deterministic_policy" or case["state"] != "system_authorized":
            raise ApprovalError("invalid action authority")

    def apply(self, request: ActionRequest, owner: str = "operator", simulate_unknown_after_commit: bool = False) -> ActionResult:
        existing = self.store.effect_by_key(request.idempotency_key)
        if existing:
            return ActionResult("accepted_existing_effect", existing["effect_id"], existing)
        self._authorize(request)
        if not self.store.acquire_lease("range:ledger", owner, 30):
            raise OverlapError("ledger range held by another owner")
        effect_id = "eff-" + hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16]
        data = asdict(request)
        with self.store.db:
            self.store.db.execute("INSERT INTO effects VALUES(?,?,?,?,?,?,datetime('now'))",
                                  (effect_id, request.case_id, request.action, request.amount, request.currency, request.idempotency_key))
        readback = self.store.effect_by_key(request.idempotency_key)
        if simulate_unknown_after_commit:
            self.store.record_attempt(request.idempotency_key, data, "effect_unknown", None)
            return ActionResult("effect_unknown", None, None)
        self.store.record_attempt(request.idempotency_key, data, "accepted", effect_id)
        self.store.event("action_readback", {"case_id": request.case_id, "effect_id": effect_id, "idempotency_key": request.idempotency_key})
        return ActionResult("accepted", effect_id, readback)

    def replay_uncertain(self, key: str) -> ActionResult:
        attempt = self.store.uncertain_attempt(key)
        if not attempt:
            raise KeyError(f"no uncertain attempt: {key}")
        # Native read-before-retry: the unique semantic key is queried before any write.
        existing = self.store.effect_by_key(key)
        if existing:
            self.store.record_attempt(key, json.loads(attempt["request"]), "accepted_existing_effect", existing["effect_id"])
            return ActionResult("accepted_existing_effect", existing["effect_id"], existing)
        request = ActionRequest(**json.loads(attempt["request"]))
        return self.apply(request)
