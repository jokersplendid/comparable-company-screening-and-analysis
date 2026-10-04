"""State transitions for human review decisions."""
from __future__ import annotations

from copy import deepcopy


REVIEWED = "已复核"
PENDING = "待复核"
INCLUDED = "纳入"
EXCLUDED = "不纳入"


def normalized_record(record: dict | None) -> dict:
    result = deepcopy(record) if isinstance(record, dict) else {}
    if result.get("review_status") == "复核通过":
        result["review_status"] = REVIEWED
    result.setdefault("review_status", PENDING)
    result.setdefault("final_decision", "")
    return result


def apply_action(record: dict | None, action: str) -> dict:
    """Apply one reversible bulk action while keeping state combinations valid."""
    result = normalized_record(record)
    if action == "mark_reviewed":
        result["review_status"] = REVIEWED
    elif action == "restore_pending":
        result["review_status"] = PENDING
        result["final_decision"] = ""
    elif action == "include":
        result["review_status"] = REVIEWED
        result["final_decision"] = INCLUDED
    elif action == "exclude":
        result["review_status"] = REVIEWED
        result["final_decision"] = EXCLUDED
    elif action == "clear_decision":
        result["review_status"] = PENDING
        result["final_decision"] = ""
    else:
        raise ValueError(f"未知复核操作：{action}")
    return result


def export_ready(records: list[dict]) -> bool:
    """Final export requires every analyzed pair to be reviewed and decided."""
    return bool(records) and all(
        normalized_record(record).get("review_status") == REVIEWED
        and normalized_record(record).get("final_decision") in {INCLUDED, EXCLUDED}
        for record in records
    )
