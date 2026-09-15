"""Read a guarded XLSX layout into the accrual contract; never evaluate Excel code."""
from datetime import date, datetime
from decimal import Decimal
import hashlib
from io import BytesIO
from pathlib import Path
import re
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from .accrual import evaluate_accrual
from .evaluation import day, digest, money, rows, shape, text

VERSION = "xlsx-accrual-intake-v1"


def reconcile_workbook(path, mapping, additional_adjustment, producer):
    """Mappings are preparer assertions. Return the extracted inputs with the result."""
    try:
        import openpyxl
    except ImportError as exc:
        raise ValueError('XLSX intake requires the optional dependency: pip install ".[xlsx]"') from exc
    shape(mapping, "schema_version case_id scope currency period_start period_end support_as_of history_complete checks reversal collections outstanding already_reflected", "mapping")
    if type(mapping["schema_version"]) is not int or mapping["schema_version"] != 1:
        raise ValueError("mapping.schema_version: expected 1")
    rows(mapping["checks"], "mapping.checks")
    if not mapping["checks"]:
        raise ValueError("mapping.checks: at least one source/layout assertion required")
    raw = Path(path).read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    # One immutable byte snapshot supplies both the hash and all reads.
    try:
        workbook = openpyxl.load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    except (BadZipFile, ParseError, KeyError, OSError, ValueError) as exc:
        raise ValueError("workbook: cannot read XLSX; supply a valid, unencrypted XLSX file") from exc
    observations = {}
    source_prefix = f"{Path(path).name}@sha256:{source_hash}"

    def cell(ref):
        shape(ref, "sheet cell", "cell reference")
        text(ref["sheet"], "cell reference.sheet")
        coordinate = ref["cell"]
        if not isinstance(coordinate, str) or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", coordinate):
            raise ValueError("cell reference.cell: expected an uppercase single-cell address, e.g. B12")
        col, row = openpyxl.utils.cell.coordinate_from_string(coordinate)
        if openpyxl.utils.cell.column_index_from_string(col) > 16384 or row > 1048576:
            raise ValueError(f"cell reference: outside XLSX bounds: {coordinate}")
        if ref["sheet"] not in workbook.sheetnames:
            raise ValueError(f"workbook: missing sheet {ref['sheet']!r}; review the mapping")
        key = f"{ref['sheet']}!{coordinate}"
        if key not in observations:
            value = workbook[ref["sheet"]][coordinate].value
            observations[key] = value.isoformat() if isinstance(value, (date, datetime)) else value
        return observations[key], key

    def amount(spec, path):
        shape(spec, "cells sign", path)
        rows(spec["cells"], f"{path}.cells")
        if not spec["cells"]:
            raise ValueError(f"{path}.cells: at least one cell required")
        if type(spec["sign"]) is not int or spec["sign"] not in (-1, 1):
            raise ValueError(f"{path}.sign: expected 1 or -1")
        total = Decimal(0)
        refs = []
        for ref in spec["cells"]:
            value, key = cell(ref)
            if key in refs:
                raise ValueError(f"{path}: duplicate amount cell {key}")
            refs.append(key)
            if isinstance(value, str) and value.startswith("="):
                raise ValueError(f"{key}: formula amounts are unsupported, even with caches; map reviewed literal components and guard the formula explicitly")
            if type(value) not in (int, float):
                raise ValueError(f"{key}: expected a numeric literal; blank/text/error cells are not zero")
            number = Decimal(str(value))
            if not number.is_finite() or abs(number) >= Decimal("1000000000000") or number != number.quantize(Decimal("0.01")):
                raise ValueError(f"{key}: expected a finite amount below one trillion with at most two decimal places; no rounding applied")
            total += number
        normalized = "0.00" if total == 0 else format(total * spec["sign"], ".2f")
        money(normalized, path)
        return normalized, refs

    def item(spec, path, dated=False):
        shape(spec, "id amount note date" if dated else "id amount note", path)
        text(spec["note"], f"{path}.note")
        value, refs = amount(spec["amount"], f"{path}.amount")
        result = {"id": spec["id"], "amount": value}
        if dated:
            raw_date, ref = cell(spec["date"])
            # Excel midnight datetime and literal ISO date are supported; times aren't silently dropped.
            if isinstance(raw_date, str) and raw_date.endswith("T00:00:00"):
                raw_date = raw_date.removesuffix("T00:00:00")
            day(raw_date, ref)
            result["date"] = raw_date
            refs.append(ref)
        result["source"] = source_prefix + " / " + ", ".join(refs) + " / " + spec["note"]
        return result

    try:
        for assertion in mapping["checks"]:
            shape(assertion, "ref equals", "mapping.checks[]")
            expected = assertion["equals"]
            if type(expected) not in (str, int, float):
                raise ValueError("mapping.checks[].equals: expected text or numeric literal")
            observed, ref = cell(assertion["ref"])
            if type(observed) is bool or observed != expected:
                raise ValueError(f"{ref}: source/layout assertion changed; expected {expected!r}, observed {observed!r}; review the mapping")
        case = {k: mapping[k] for k in ("schema_version", "case_id", "scope", "currency", "period_start", "period_end", "support_as_of", "history_complete")}
        case["reversal"] = item(mapping["reversal"], "mapping.reversal", True)
        for key in ("collections", "outstanding"):
            rows(mapping[key], f"mapping.{key}")
            case[key] = [item(value, f"mapping.{key}[{i}]", key == "collections") for i, value in enumerate(mapping[key])]
        case["already_reflected"] = None if mapping["already_reflected"] is None else item(mapping["already_reflected"], "mapping.already_reflected")
        proposal = {"schema_version": 1, "case_id": case["case_id"], "scope": case["scope"],
                    "currency": case["currency"], "date": case["period_end"], "producer": producer,
                    "additional_adjustment": additional_adjustment}
        result = evaluate_accrual(case, proposal)
        del result["result_hash"]
        result["intake"] = {"version": VERSION, "workbook_sha256": source_hash, "mapping_hash": digest(mapping),
                            "mapping": mapping, "cells": observations, "case": case, "proposal": proposal,
                            "limit": "Mapping, signs, allocation, cutoff and completeness are preparer assertions; guards do not establish their accounting correctness. Formulas are not recalculated."}
        result["result_hash"] = digest(result)
        return result
    finally:
        workbook.close()
