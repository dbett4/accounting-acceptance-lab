from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .store import ApprovalError, Store


class GenerationConflict(RuntimeError): pass
class OverlapError(RuntimeError): pass
class IdempotencyConflict(RuntimeError): pass
class BoundsError(ValueError): pass


def proposal_digest(proposal: dict) -> str:
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class ActionRequest:
    case_id: str
    action: str
    amount: str
    currency: str
    idempotency_key: str
    expected_generation: int
    authority: str
    proposal_digest: str | None = None


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

    @staticmethod
    def _canonical_request(request: ActionRequest) -> tuple[dict, str]:
        try:
            amount = Decimal(request.amount).quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise BoundsError("invalid amount") from exc
        fields = {
            "action": request.action,
            "amount": format(amount, "f"),
            "authority": request.authority,
            "case_id": request.case_id,
            "currency": request.currency,
            "expected_generation": request.expected_generation,
            "proposal_digest": request.proposal_digest,
        }
        encoded = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
        return fields, hashlib.sha256(encoded).hexdigest()

    def _authorize_row(self, request: ActionRequest, case, approval, allow_consumed_generation: bool = False) -> None:
        if not case:
            raise KeyError(request.case_id)
        if case["generation"] != request.expected_generation and not (
            allow_consumed_generation and case["generation"] == request.expected_generation + 1
        ):
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
            if not approval or approval["decision"] != "approved" or not approval["actor"]:
                raise ApprovalError("attributable human approval required")
            expected = {
                "action": approval["action"], "amount": approval["amount"],
                "currency": approval["currency"], "generation": approval["generation"],
                "proposal_digest": approval["proposal_digest"],
            }
            actual = {
                "action": request.action, "amount": format(Decimal(request.amount).quantize(Decimal("0.01")), "f"),
                "currency": request.currency, "generation": request.expected_generation,
                "proposal_digest": request.proposal_digest,
            }
            if actual != expected:
                raise ApprovalError("action request does not match approved proposal scope")
            if Store.CASE_ACTIONS.get(case["case_type"]) != request.action:
                raise ApprovalError("approved action is incompatible with case type")
            payload = json.loads(case["payload"])
            current_proposal = payload.get("proposal")
            current_scope = payload.get("proposed_action")
            if not isinstance(current_proposal, dict) or proposal_digest(current_proposal) != approval["proposal_digest"]:
                raise ApprovalError("approval does not match current proposal")
            if not isinstance(current_scope, dict) or {
                "action": current_scope.get("action"),
                "amount": format(Decimal(str(current_scope.get("amount"))).quantize(Decimal("0.01")), "f"),
                "currency": current_scope.get("currency"),
            } != {"action": approval["action"], "amount": approval["amount"], "currency": approval["currency"]}:
                raise ApprovalError("approval does not match current action scope")
        elif request.authority != "deterministic_policy" or (
            case["state"] != "system_authorized"
            and not (allow_consumed_generation and case["generation"] == request.expected_generation + 1)
        ):
            raise ApprovalError("invalid action authority")
        elif Store.CASE_ACTIONS.get(case["case_type"]) != request.action:
            raise ApprovalError("deterministic action is incompatible with case type")

    def apply(self, request: ActionRequest, owner: str = "operator", simulate_unknown_after_commit: bool = False) -> ActionResult:
        fields, digest = self._canonical_request(request)
        db = self.store.db
        db.execute("BEGIN IMMEDIATE")
        try:
            case = db.execute("SELECT * FROM cases WHERE case_id=?", (request.case_id,)).fetchone()
            approval = db.execute("SELECT * FROM approvals WHERE case_id=?", (request.case_id,)).fetchone()
            self._authorize_row(request, case, approval, allow_consumed_generation=True)

            existing = db.execute("SELECT * FROM effects WHERE idempotency_key=?", (request.idempotency_key,)).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise IdempotencyConflict("idempotency key reused for a different canonical request")
                db.commit()
                return ActionResult("accepted_existing_effect", existing["effect_id"], dict(existing))

            if case["generation"] != request.expected_generation:
                raise GenerationConflict(f"expected {request.expected_generation}, found {case['generation']}")

            now = datetime.now(timezone.utc)
            expires = (now + timedelta(seconds=30)).isoformat()
            acquired = db.execute(
                "INSERT INTO leases(resource,owner,expires_at) VALUES(?,?,?) "
                "ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at "
                "WHERE leases.expires_at<=? OR leases.owner=excluded.owner",
                ("range:ledger", owner, expires, now.isoformat()),
            )
            if acquired.rowcount != 1:
                raise OverlapError("ledger range held by another owner")

            effect_id = "eff-" + hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16]
            consumed = db.execute(
                "UPDATE cases SET generation=generation+1,state='effect_applied' WHERE case_id=? AND generation=?",
                (request.case_id, request.expected_generation),
            )
            if consumed.rowcount != 1:
                raise GenerationConflict("case generation changed before effect insertion")
            db.execute(
                "INSERT INTO effects(effect_id,case_id,action,amount,currency,idempotency_key,created_at,request_digest,request_json,expected_generation,authority,proposal_digest) "
                "VALUES(?,?,?,?,?,?,datetime('now'),?,?,?,?,?)",
                (effect_id, request.case_id, request.action, fields["amount"], request.currency,
                 request.idempotency_key, digest, json.dumps(fields, sort_keys=True),
                 request.expected_generation, request.authority, request.proposal_digest),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

        readback_row = db.execute("SELECT * FROM effects WHERE idempotency_key=?", (request.idempotency_key,)).fetchone()
        readback = dict(readback_row)
        data = asdict(request)
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
        request = ActionRequest(**json.loads(attempt["request"]))
        return self.apply(request)
