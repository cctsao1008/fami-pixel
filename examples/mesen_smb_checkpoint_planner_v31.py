#!/usr/bin/env python3
"""V31 planner: V30 shared-prefix COLLECT with recursion-safe exact fallback.

V30 introduced the shared continuation tree, but its optimization fallback called
``v28._search_collect_payload`` after that symbol had been replaced by the V30
shared evaluator itself.  A missing/late cross-worker manifest could therefore
re-enter the optimizer instead of falling back to V28's independent exact root
replay.

V31 captures the original V28 evaluator before installing newer overrides and
uses a tiny process-local wrapper: while V30 evaluates one request, the V28 global
fallback symbol temporarily points at the captured exact evaluator.  Shadow
workers are single-threaded processes, so this preserves the intended fallback
without changing V29 cohort semantics or V27 PROGRESS behavior.
"""

from __future__ import annotations

import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v28 as v28
import mesen_smb_checkpoint_planner_v30 as v30


PLANNER_NAME = "v31-shared-prefix-collect-safe-fallback"
_BASE_V28_COLLECT_SEARCH = v28._search_collect_payload


def _search_collect_payload_shared_safe(*args, **kwargs):
    installed = v28._search_collect_payload
    v28._search_collect_payload = _BASE_V28_COLLECT_SEARCH
    try:
        return v30._search_collect_payload_shared(*args, **kwargs)
    finally:
        v28._search_collect_payload = installed


def _install_v31_overrides() -> None:
    v30._install_v30_overrides()
    v28._search_collect_payload = _search_collect_payload_shared_safe

    v23.PLANNER_NAME = PLANNER_NAME
    v23.authority_main = v30.authority_main
    v23.__file__ = __file__


def main() -> int:
    _install_v31_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
