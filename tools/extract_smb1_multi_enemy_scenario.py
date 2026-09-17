#!/usr/bin/env python3
"""Extract a deterministic SMB1 scenario at a clean projected multi-enemy landing guard.

This is a semantic wrapper over ``extract_smb1_scenario.py`` for issue #38.
The default selector requires all of the following:

- ``landing_enemy_count >= N``;
- the live planner actually selected a ``landing-zone-*`` guard; and
- the nearest enemy cluster itself contains at least ``N`` enemies and lies
  fully inside the configured landing corridor.

That last constraint rejects current-contact/transient states such as
``cluster:1@0..0`` even when two other enemies happen to occupy the landing
corridor. The named regression is intended to exercise a projected multi-Goomba
landing hazard, not an already-overlapping enemy contact state. No World 1-1 X
coordinate is hard-coded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def _load_timeline(path: Path) -> list[dict]:
    records: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON at {path}:{line_number}: {exc}") from exc
    if not records:
        raise SystemExit(f"timeline is empty: {path}")
    return records


def _field(record: dict, key: str):
    """Read one landing field from timeline top level first, then radar metadata."""

    for source in (record, record.get("radar") or {}):
        try:
            value = source.get(key)
        except AttributeError:
            continue
        if value is not None:
            return value
    return None


def _int_field(record: dict, key: str, default: int | None = None) -> int | None:
    value = _field(record, key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def landing_enemy_count(record: dict) -> int:
    """Read landing-corridor enemy count from either timeline or radar metadata."""

    value = _int_field(record, "landing_enemy_count", 0)
    return max(0, int(value or 0))


def landing_guard_mode(record: dict) -> str:
    """Return the selected live landing guard, or an empty string when absent."""

    value = record.get("guard_mode")
    if value is None:
        return ""
    return str(value)


def is_active_landing_guard(record: dict) -> bool:
    return landing_guard_mode(record).startswith("landing-zone-")


def projected_cluster_fields(record: dict) -> tuple[int, int | None, int | None, int, int]:
    """Return nearest-cluster count/range and configured landing corridor."""

    count = max(0, int(_int_field(record, "nearest_cluster_count", 0) or 0))
    start = _int_field(record, "nearest_cluster_start_dx")
    end = _int_field(record, "nearest_cluster_end_dx")
    near = int(_int_field(record, "landing_corridor_start_dx", 96) or 96)
    far = int(_int_field(record, "landing_corridor_end_dx", 160) or 160)
    return count, start, end, near, far


def is_clean_projected_multi_enemy_cluster(record: dict, minimum: int) -> bool:
    """Require the nearest multi-enemy cluster itself to lie in the landing corridor."""

    count, start, end, near, far = projected_cluster_fields(record)
    if count < int(minimum) or start is None or end is None:
        return False
    return near <= start <= end <= far


def select_multi_enemy_record(
    records: list[dict],
    minimum: int,
    *,
    require_active_guard: bool = True,
    require_projected_cluster: bool = True,
) -> dict:
    if minimum <= 0:
        raise ValueError("minimum must be > 0")

    matches = [record for record in records if landing_enemy_count(record) >= int(minimum)]
    if require_active_guard:
        matches = [record for record in matches if is_active_landing_guard(record)]
    if require_projected_cluster:
        matches = [
            record
            for record in matches
            if is_clean_projected_multi_enemy_cluster(record, int(minimum))
        ]

    if not matches:
        qualifiers: list[str] = []
        if require_active_guard:
            qualifiers.append("an active landing-zone guard")
        if require_projected_cluster:
            qualifiers.append("a nearest multi-enemy cluster fully inside the landing corridor")
        suffix = ""
        if qualifiers:
            suffix = " with " + " and ".join(qualifiers)
        raise SystemExit(
            f"no timeline record has landing_enemy_count >= {int(minimum)}{suffix}"
        )
    return min(matches, key=lambda record: int(record.get("generation", 1 << 30)))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract a deterministic SMB1 scenario at a clean projected multi-enemy landing event"
    )
    p.add_argument("timeline", type=Path, help="live-run timeline.jsonl")
    p.add_argument("checkpoint_dir", help="matching Runtime IPC run-* directory, or literal 'auto'")
    p.add_argument("scenario_id", help="output scenario id, e.g. multi-goomba-landing-v26")
    p.add_argument(
        "--landing-enemies-at-least",
        type=int,
        default=2,
        help="minimum landing-corridor enemy count (default: 2)",
    )
    p.add_argument(
        "--allow-passive-count-only",
        action="store_true",
        help=(
            "diagnostic mode: allow an incidental landing_enemy_count match even when the live "
            "planner did not select a landing-zone guard; clean projected-cluster semantics "
            "remain required"
        ),
    )
    p.add_argument(
        "--allow-contact-or-split-cluster-root",
        action="store_true",
        help=(
            "diagnostic mode: do not require the nearest multi-enemy cluster to lie fully "
            "inside the landing corridor; not valid for #38 acceptance"
        ),
    )
    p.add_argument(
        "--lead-generations",
        type=int,
        default=0,
        help="rewind this many control generations before the selected event (default: 0)",
    )
    p.add_argument(
        "--stable-root",
        action="store_true",
        help="ask the generic extractor to rewind to a grounded/control-stable root",
    )
    args = p.parse_args()
    if args.landing_enemies_at_least <= 0:
        p.error("--landing-enemies-at-least must be > 0")
    if args.lead_generations < 0:
        p.error("--lead-generations must be >= 0")
    return args


def worker(args: argparse.Namespace) -> int:
    timeline = args.timeline.expanduser().resolve()
    records = _load_timeline(timeline)
    require_active_guard = not bool(args.allow_passive_count_only)
    require_projected_cluster = not bool(args.allow_contact_or_split_cluster_root)
    selected = select_multi_enemy_record(
        records,
        int(args.landing_enemies_at_least),
        require_active_guard=require_active_guard,
        require_projected_cluster=require_projected_cluster,
    )
    generation = int(selected.get("generation", -1))
    if generation < 0:
        raise SystemExit("selected multi-enemy timeline record has no valid generation")

    count = landing_enemy_count(selected)
    guard = landing_guard_mode(selected)
    cluster_count, cluster_start, cluster_end, corridor_start, corridor_end = projected_cluster_fields(
        selected
    )
    print(
        f"MultiEnemy : generation={generation} frame={selected.get('native_frame')} "
        f"X={selected.get('mario_x')} landing_enemy_count={count} "
        f"cluster={cluster_count}@{cluster_start}..{cluster_end} "
        f"corridor={corridor_start}..{corridor_end} guard={guard or '-'}",
        flush=True,
    )

    generic = Path(__file__).resolve().with_name("extract_smb1_scenario.py")
    cmd = [
        sys.executable,
        str(generic),
        str(timeline),
        str(args.checkpoint_dir),
        str(args.scenario_id),
        "--generation",
        str(generation),
        "--lead-generations",
        str(int(args.lead_generations)),
    ]
    if args.stable_root:
        cmd.append("--stable-root")

    completed = subprocess.run(cmd, check=False)
    if int(completed.returncode) != 0:
        return int(completed.returncode)

    manifest_path = Path("build/scenarios") / str(args.scenario_id) / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["selection_landing_enemy_count"] = int(count)
            manifest["selection_landing_enemies_at_least"] = int(args.landing_enemies_at_least)
            manifest["selection_guard_mode"] = guard or None
            manifest["selection_action"] = selected.get("action")
            manifest["selection_nearest_cluster_count"] = int(cluster_count)
            manifest["selection_nearest_cluster_start_dx"] = cluster_start
            manifest["selection_nearest_cluster_end_dx"] = cluster_end
            manifest["selection_landing_corridor_start_dx"] = int(corridor_start)
            manifest["selection_landing_corridor_end_dx"] = int(corridor_end)
            if require_active_guard and require_projected_cluster:
                selection_policy = "active-landing-guard+projected-multi-enemy-cluster"
            elif require_active_guard:
                selection_policy = "active-landing-guard+enemy-count"
            else:
                selection_policy = "landing-enemy-count"
            manifest["selection_policy"] = selection_policy
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    return 0


def main() -> int:
    return worker(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
