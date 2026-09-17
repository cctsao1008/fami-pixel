#!/usr/bin/env python3
"""Extract a deterministic SMB1 scenario at an active multi-enemy landing guard.

This is a semantic wrapper over ``extract_smb1_scenario.py`` for issue #38.
The default selector requires both:

- ``landing_enemy_count >= N``; and
- the live planner actually selected a ``landing-zone-*`` guard at that control root.

That distinction matters. Merely observing two enemies in the fixed landing
corridor can occur at incidental/non-actionable states. The deterministic gate
should replay the state where the live landing policy actually took authority,
without hard-coding a World 1-1 X coordinate.
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


def landing_enemy_count(record: dict) -> int:
    """Read landing-corridor enemy count from either timeline or radar metadata."""

    for source in (record, record.get("radar") or {}):
        try:
            value = source.get("landing_enemy_count")
        except AttributeError:
            continue
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def landing_guard_mode(record: dict) -> str:
    """Return the selected live landing guard, or an empty string when absent."""

    value = record.get("guard_mode")
    if value is None:
        return ""
    return str(value)


def is_active_landing_guard(record: dict) -> bool:
    return landing_guard_mode(record).startswith("landing-zone-")


def select_multi_enemy_record(
    records: list[dict],
    minimum: int,
    *,
    require_active_guard: bool = True,
) -> dict:
    if minimum <= 0:
        raise ValueError("minimum must be > 0")

    matches = [record for record in records if landing_enemy_count(record) >= int(minimum)]
    if require_active_guard:
        matches = [record for record in matches if is_active_landing_guard(record)]

    if not matches:
        suffix = " with an active landing-zone guard" if require_active_guard else ""
        raise SystemExit(
            f"no timeline record has landing_enemy_count >= {int(minimum)}{suffix}"
        )
    return min(matches, key=lambda record: int(record.get("generation", 1 << 30)))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract a deterministic SMB1 scenario at an active multi-enemy landing event"
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
            "allow an incidental landing_enemy_count match even when the live planner did not "
            "select a landing-zone guard; not recommended for #38 acceptance"
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
    selected = select_multi_enemy_record(
        records,
        int(args.landing_enemies_at_least),
        require_active_guard=require_active_guard,
    )
    generation = int(selected.get("generation", -1))
    if generation < 0:
        raise SystemExit("selected multi-enemy timeline record has no valid generation")

    count = landing_enemy_count(selected)
    guard = landing_guard_mode(selected)
    print(
        f"MultiEnemy : generation={generation} frame={selected.get('native_frame')} "
        f"X={selected.get('mario_x')} landing_enemy_count={count} guard={guard or '-'}",
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
            manifest["selection_policy"] = (
                "active-landing-guard+enemy-count"
                if require_active_guard
                else "landing-enemy-count"
            )
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    return 0


def main() -> int:
    return worker(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
