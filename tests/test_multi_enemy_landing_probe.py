from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "smb1_multi_enemy_landing_probe.py"
_SPEC = spec_from_file_location("smb1_multi_enemy_landing_probe_test", _TOOL)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
try:
    _SPEC.loader.exec_module(_MOD)
finally:
    sys.modules.pop(_SPEC.name, None)


def test_manifest_multi_enemy_count_requires_explicit_fixture_metadata() -> None:
    assert _MOD.manifest_multi_enemy_count({"selection_landing_enemy_count": 2}) == 2
    assert _MOD.manifest_multi_enemy_count({"selection_landing_enemy_count": "3"}) == 3
    assert _MOD.manifest_multi_enemy_count({}) == 0


def test_manifest_projected_cluster_requires_multi_enemy_cluster_inside_corridor() -> None:
    clean = {
        "selection_nearest_cluster_count": 2,
        "selection_nearest_cluster_start_dx": 98,
        "selection_nearest_cluster_end_dx": 136,
        "selection_landing_corridor_start_dx": 96,
        "selection_landing_corridor_end_dx": 160,
    }
    contact = {
        "selection_nearest_cluster_count": 1,
        "selection_nearest_cluster_start_dx": 0,
        "selection_nearest_cluster_end_dx": 0,
        "selection_landing_corridor_start_dx": 96,
        "selection_landing_corridor_end_dx": 160,
    }
    split = {
        "selection_nearest_cluster_count": 2,
        "selection_nearest_cluster_start_dx": 98,
        "selection_nearest_cluster_end_dx": 170,
        "selection_landing_corridor_start_dx": 96,
        "selection_landing_corridor_end_dx": 160,
    }

    assert _MOD.manifest_has_clean_projected_cluster(clean, 2)
    assert not _MOD.manifest_has_clean_projected_cluster(contact, 2)
    assert not _MOD.manifest_has_clean_projected_cluster(split, 2)
    assert not _MOD.manifest_has_clean_projected_cluster({}, 2)


def test_expected_live_plan_follows_guard_mode() -> None:
    assert _MOD.expected_live_plan_name("landing-zone-extend[landing:2]") == "live_cluster_extend_8"
    assert _MOD.expected_live_plan_name("landing-zone-escape[landing:2]") == "live_cluster_jump_16"
    assert _MOD.expected_live_plan_name("forward-model-partial[landed]") is None


def test_output_accepts_only_generic_probe_safe_selection_marker() -> None:
    assert _MOD.output_has_safe_selection(
        "long_jump event=landed status=RESOLVED\n"
        "SELECT     : long_jump -> landed key=(3, 0, 92, 92)\n"
        "TrajectoryProbe: DONE\n"
    )
    assert not _MOD.output_has_safe_selection(
        "NO SAFE    : no Mesen-resolved non-death trajectory exists\n"
        "TrajectoryProbe: DONE\n"
    )
    assert not _MOD.output_has_safe_selection(
        "PROVISIONAL: run -> horizon\nTrajectoryProbe: DONE\n"
    )


def test_live_guard_success_requires_matching_exact_action_and_resolved_terminal() -> None:
    extend = "landing-zone-extend[landing:2]"
    escape = "landing-zone-escape[landing:2]"

    assert _MOD.output_has_live_guard_success(
        "live_cluster_extend_8 event=landed status=RESOLVED\n",
        extend,
    )
    assert _MOD.output_has_live_guard_success(
        "live_cluster_jump_16 event=win status=RESOLVED\n",
        escape,
    )
    assert not _MOD.output_has_live_guard_success(
        "live_cluster_extend_8 event=horizon status=UNRESOLVED\n",
        extend,
    )
    assert not _MOD.output_has_live_guard_success(
        "long_jump event=landed status=RESOLVED\n",
        extend,
    )


def test_control_signature_tracks_authoritative_branch_state() -> None:
    observation = SimpleNamespace(
        mario_x_abs=1167,
        mario_y=144,
        game_engine_subroutine=0x08,
        player_state=1,
        player_x_speed=0x19,
        player_y_speed=0xF8,
    )
    assert _MOD._control_signature(observation) == (1167, 144, 0x08, 1, 0x19, 0xF8)


def test_diagnostic_command_uses_disposable_child_worker() -> None:
    args = SimpleNamespace(
        rom=Path("game.nes"),
        scenario_dir=Path("scenario"),
        dll=Path("MesenCore.dll"),
        home=Path("mesen-home"),
        step_timeout=2.0,
    )
    command = _MOD._diagnostic_command(args)
    assert command[0] == sys.executable
    assert "--diag-worker" in command
    assert "--step-timeout" in command


def test_diagnostic_payload_parser_requires_single_machine_marker() -> None:
    output = (
        "RootState  : engine=0x08 control=1\n"
        "RootControlDiagnostic: {\"root_player_control\":true,\"responsive\":true,\"root_engine\":8}\n"
    )
    payload = _MOD._parse_diagnostic_output(output)
    assert payload["root_player_control"] is True
    assert payload["responsive"] is True
    assert payload["root_engine"] == 8
