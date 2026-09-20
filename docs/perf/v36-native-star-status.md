# V36 native exact-boundary Star MPC status

Validated locally on Windows against the current MesenCE native speculative runner.

## Passed gates

- Unit suite: `9 passed in 0.25s` before endpoint-only radar optimization.
- Same-root V35 vs V36 semantic comparison at scenario root frame 949, Mario X=1616:
  - V35 live-core exact: 574.359 ms
  - V36 native exact-boundary: 118.862 ms
  - speedup: 4.83x
  - semantic comparison: PASS
- Comparison covers all eight Star candidates and selected-plan policy semantics, excluding timing fields.

## Current optimization

The first full V36 decision remained above the issue #41 <100 ms p50 stretch target even though the native 8x4f substrate itself measured ~46.6 ms p50. The main avoidable Python-side cost was decoding the full SMB radar for every one of 32 per-boundary RAM witnesses.

V36 now keeps per-frame authority semantics but uses only the capability bytes required for collection proof (`player_status`, `star_invincible_timer`) while traversing witnesses. It decodes the full scene radar exactly once at the semantic stop endpoint for target tracking, enemy clearance, and reward ranking.

This does not alter V25/V35 policy ordering or proof authority:

- death / level-complete are still derived per exact frame from decoded SMB state;
- Star collection is still proven from native `StarInvincibleTimer` increase;
- early stop still ignores later already-buffered witnesses;
- final reward ranking still consumes the full radar and active reward target at the same semantic endpoint.

## Next local gates

1. rerun the focused unit suite after endpoint-only radar optimization;
2. rerun `smb1_v36_native_star_compare.py` and require semantic PASS;
3. run `smb1_v36_native_star_scenario_replay.py` to prove actual Star collection while reporting full-decision p50/p95 latency;
4. only after those pass, consider PR #44 ready for broader integration review.
