from src.roadmap_status import build_status


def test_roadmap_status_is_repository_wide_and_gta_gated():
    phases = build_status(test_count=1682, safety_freeze=True, deployment_ready=True)
    by_phase = {phase.phase: phase for phase in phases}
    assert by_phase["F"].status == "complete"
    assert by_phase["GTA"].status == "gated"
    assert by_phase["GTA"].blocker == "live promotion forbidden"
