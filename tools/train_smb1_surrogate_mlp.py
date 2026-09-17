#!/usr/bin/env python3
"""Train, evaluate, and persist the dependency-free SMB1 surrogate baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

from fami_pixel.learning import load_jsonl_records
from fami_pixel.learning.baseline_split import (
    split_rollout_records,
    split_rollout_records_stratified,
)
from fami_pixel.learning.model_artifact import (
    build_dataset_manifest,
    build_metadata,
    build_metrics,
    next_versioned_artifact_dir,
    write_model_artifact,
)
from fami_pixel.learning.tiny_mlp import TinySurrogateMLP, evaluate_model, feature_vector


TRAINING_SCRIPT_VERSION = "smb1-tiny-surrogate-trainer-v2"
_VERSIONED_NAME_RE = re.compile(r"^.+-v[0-9]{3,}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train offline SMB1 tiny surrogate MLP baseline")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--hidden", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=800)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=22)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("build/models"),
        help="root for auto-numbered smb1-surrogate-vNNN artifact directories",
    )
    parser.add_argument(
        "--output-artifact",
        type=Path,
        help="explicit versioned artifact directory (name must end in -vNNN)",
    )
    parser.add_argument(
        "--artifact-prefix",
        default="smb1-surrogate",
        help="prefix used when auto-numbering an artifact under --output-root",
    )
    parser.add_argument(
        "--output-model",
        type=Path,
        help="optional extra standalone model.json copy for legacy scripts",
    )
    parser.add_argument("--note", help="optional human experiment note stored in metadata.json")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="repeatable experiment tag stored in metadata.json",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="print training progress every N epochs; 0 disables progress output",
    )
    args = parser.parse_args()
    if args.epochs <= 0:
        parser.error("--epochs must be > 0")
    if args.hidden <= 0:
        parser.error("--hidden must be > 0")
    if args.learning_rate <= 0:
        parser.error("--learning-rate must be > 0")
    if args.progress_every < 0:
        parser.error("--progress-every must be >= 0")
    if not str(args.artifact_prefix).strip():
        parser.error("--artifact-prefix must not be empty")
    if args.output_artifact is not None and not _VERSIONED_NAME_RE.match(args.output_artifact.name):
        parser.error("--output-artifact directory name must end in -vNNN")
    return args


def _measure_inference_latency_us(model: TinySurrogateMLP, rows: list[dict]) -> float | None:
    if not rows:
        return None
    sample = rows[: min(128, len(rows))]
    repeats = 5
    started = time.perf_counter()
    for _ in range(repeats):
        for row in sample:
            model.predict(row)
    elapsed = time.perf_counter() - started
    return elapsed * 1_000_000.0 / (len(sample) * repeats)


def main() -> int:
    args = parse_args()
    records = load_jsonl_records(args.paths)
    if not records:
        raise SystemExit("no rollout records found")

    model_split = split_rollout_records_stratified(records)
    ood_split = split_rollout_records(records)
    if not model_split["train"]:
        raise SystemExit("model-selection training split is empty")

    input_size = len(feature_vector(model_split["train"][0]))
    model = TinySurrogateMLP(input_size, args.hidden, seed=args.seed)
    started = time.perf_counter()

    def progress(epoch: int, total: int) -> None:
        if args.progress_every <= 0:
            return
        if epoch != total and epoch % args.progress_every != 0:
            return
        elapsed = time.perf_counter() - started
        print(
            f"training epoch {epoch:4d}/{total} elapsed={elapsed:7.1f}s",
            file=sys.stderr,
            flush=True,
        )

    model.fit(
        model_split["train"],
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        seed=args.seed,
        progress_callback=progress,
    )
    training_seconds = time.perf_counter() - started

    train_metrics = evaluate_model(model, model_split["train"])
    validation_metrics = evaluate_model(model, model_split["validation"])
    test_metrics = evaluate_model(model, model_split["test"])
    chronological_metrics = evaluate_model(model, ood_split["test"])
    latency_us = _measure_inference_latency_us(model, model_split["validation"] or model_split["train"])

    if args.output_artifact is None:
        artifact_dir = next_versioned_artifact_dir(
            args.output_root,
            prefix=str(args.artifact_prefix),
        )
    else:
        artifact_dir = args.output_artifact.expanduser().resolve()

    repo_root = Path(__file__).resolve().parents[1]
    metadata = build_metadata(
        model_id=artifact_dir.name,
        hidden_size=args.hidden,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        seed=args.seed,
        training_script=str(Path(__file__).resolve().relative_to(repo_root)),
        training_script_version=TRAINING_SCRIPT_VERSION,
        repo_root=repo_root,
        note=args.note,
        tags=args.tag,
    )
    metrics = build_metrics(
        train=train_metrics,
        validation=validation_metrics,
        test=test_metrics,
        chronological_test=chronological_metrics,
        training_seconds=training_seconds,
        inference_latency_us_per_record=latency_us,
    )
    dataset_manifest = build_dataset_manifest(
        args.paths,
        model_split=model_split,
        ood_split=ood_split,
    )
    artifact_dir = write_model_artifact(
        artifact_dir,
        model=model,
        metadata=metadata,
        metrics=metrics,
        dataset_manifest=dataset_manifest,
    )

    legacy_model_path = None
    if args.output_model is not None:
        legacy_model_path = args.output_model.expanduser().resolve()
        model.save_json(legacy_model_path)

    report = {
        "artifact_dir": str(artifact_dir),
        "model_artifact": str(artifact_dir / "model.json"),
        "metadata": str(artifact_dir / "metadata.json"),
        "metrics": str(artifact_dir / "metrics.json"),
        "dataset_manifest": str(artifact_dir / "dataset_manifest.json"),
        "legacy_model_copy": None if legacy_model_path is None else str(legacy_model_path),
        "training_seconds": training_seconds,
        "inference_latency_us_per_record": latency_us,
        "model_selection": {
            "train": train_metrics,
            "validation": validation_metrics,
            "test": test_metrics,
        },
        "ood_stress_test": {
            "chronological_test": chronological_metrics,
        },
        "authority_boundary": metadata["authority_boundary"],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
