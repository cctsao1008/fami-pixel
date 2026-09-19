# Mesen exact-control boundary findings

Representative Windows/Mesen measurements captured on 2026-09-19 for the current SMB1 exact-control path.

## Baseline

Normal pacing kept synchronous debugger stepping effectively realtime:

- `step_sync_1`: ~16.6-16.7 ms/frame (~60 fps)
- `step_sync_batch(4)`: ~16.6-16.7 ms/frame (~60 fps)
- V35-shaped 8x4f branch search: ~532-544 ms/decision

RAM/state/radar reads were tens of microseconds, while save/load round trips were only a few milliseconds. They are not the primary latency source at this stage.

## MaximumSpeed A/B

With Mesen `EmulationFlags::MaximumSpeed` enabled:

- scalar 1-frame step improved only modestly: ~14.1 ms/frame in the second run;
- batched 4-frame step improved to ~4.30 ms/frame (~229 fps);
- V35-shaped 8x4f slot-restored scalar branch search: ~498 ms/decision;
- V35-shaped 8x4f slot-restored batched branch search: ~227.9 ms/decision.

Measured p50 gains from the second run:

- normal batch -> MaximumSpeed batch branch: 2.336x;
- MaximumSpeed scalar -> MaximumSpeed batched branch: 2.185x;
- per-frame batch gain under MaximumSpeed: 3.280x.

A scripted 32-frame input replay (`RIGHT`, `RIGHT+A`, `RIGHT+B`, `RELEASE`) produced identical decoded SMB1 state under normal and MaximumSpeed pacing.

## Interpretation

The dominant cost is not SMB1 state decoding, radar, JSON, or file-backed checkpoints. It is synchronous debugger stepping and its wall-clock pacing / stop-resume synchronization. `MaximumSpeed` helps substantially only when several emulated frames are executed per debugger step.

Directly replacing V35's scalar reward-prefix evaluator with endpoint-only 4-frame batching is not behavior-preserving: the current evaluator observes every frame and may stop on death, level completion, or reward collection, and some reward chunks change input within the 4-frame quantum. Performance work therefore must preserve exact per-frame evidence semantics.

The lowest-risk next implementation is a barriered parallel current-root Star MPC: pause live authority, distribute the eight exact candidates across persistent Mesen worker processes rooted at the same checkpoint, keep the existing per-frame evaluator semantics, optionally enable `MaximumSpeed` inside speculative worker cores, gather the exact results, then resume authority. This uses host CPU parallelism without weakening Mesen authority or changing candidate ranking semantics.

Related: #24, #35, #40.
