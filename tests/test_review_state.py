from src.review_state import apply_action, export_ready


def test_bulk_actions_are_reversible_and_keep_states_consistent():
    original={"review_status":"待复核","final_decision":"","analysis_draft":"人工分析"}
    included=apply_action(original,"include")
    assert included["review_status"]=="已复核"
    assert included["final_decision"]=="纳入"
    restored=apply_action(included,"restore_pending")
    assert restored["review_status"]=="待复核"
    assert restored["final_decision"]==""
    assert restored["analysis_draft"]=="人工分析"


def test_export_ready_requires_every_row_reviewed_and_decided():
    assert export_ready([
        {"review_status":"已复核","final_decision":"纳入"},
        {"review_status":"已复核","final_decision":"不纳入"},
    ])
    assert not export_ready([
        {"review_status":"已复核","final_decision":"纳入"},
        {"review_status":"已复核","final_decision":""},
    ])
