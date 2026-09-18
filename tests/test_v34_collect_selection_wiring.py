from pathlib import Path


def test_v34_routes_collect_selection_and_result_shaping_through_stable_planning():
    source = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "mesen_smb_checkpoint_planner_v34.py"
    ).read_text(encoding="utf-8")

    assert "selection = select_collect_proof(" in source
    assert "selector=select_lineage_collect_proof" in source
    assert "return shape_eager_collect_result(" in source

    # The historical lineage/proof selector remains the supplied authority, but
    # V34 no longer invokes it directly at either reward-stage or anchor admission.
    assert "selection = select_lineage_collect_proof(" not in source
