"""Versioned, auditable artifacts for learned SMB1 surrogate models.

The learned model is advisory only.  This module packages an inference model
with the provenance, evaluation metrics, and dataset fingerprints needed to
explain where the weights came from and whether they are compatible with the
current feature extractor.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Iterable

from .baseline_split import summarize_partition
from .rollout_dataset import ROLLOUT_SCHEMA_VERSION, load_jsonl_records
from .tiny_mlp import FEATURE_SCHEMA_ID, MODEL_FORMAT, OUTPUT_SCHEMA, TinySurrogateMLP


ARTIFACT_FORMAT = "fami-pixel-surrogate-artifact-v1"
METRICS_FORMAT = "fami-pixel-surrogate-metrics-v1"
DATASET_MANIFEST_FORMAT = "fami-pixel-surrogate-dataset-manifest-v1"
TARGET_SCHEMA_ID = "smb1-surrogate-targets-v1"
AUTHORITY_BOUNDARY = (
    "learned model ranks/prunes candidates; exact Mesen rollout provides "
    "authoritative trajectory evidence; live Mesen remains machine authority"
)
_REQUIRED_FILES = (
    "model.json",
    "metadata.json",
    "metrics.json",
    "dataset_manifest.json",
)
_VERSION_RE = re.compile(r"^(?P<prefix>.+)-v(?P<version>[0-9]{3,})$")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _git_command(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def git_provenance(repo_root: Path | None = None) -> dict:
    root = Path.cwd() if repo_root is None else Path(repo_root)
    root = root.expanduser().resolve()
    commit = _git_command(root, "rev-parse", "HEAD")
    status = _git_command(root, "status", "--porcelain")
    return {
        "git_commit_sha": commit,
        "git_dirty": None if status is None else bool(status),
    }


def next_versioned_artifact_dir(
    root: Path,
    *,
    prefix: str = "smb1-surrogate",
) -> Path:
    """Return the next unused ``<prefix>-vNNN`` directory under *root*."""
    root = Path(root).expanduser().resolve()
    highest = 0
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            match = _VERSION_RE.match(child.name)
            if match is None or match.group("prefix") != prefix:
                continue
            highest = max(highest, int(match.group("version")))
    return root / f"{prefix}-v{highest + 1:03d}"


def _root_group_count(records: Iterable[dict]) -> int:
    groups = {
        (str(row.get("source", "")), int(row["generation"]))
        for row in records
        if row.get("generation") is not None
    }
    return len(groups)


def build_dataset_manifest(
    paths: Iterable[Path],
    *,
    model_split: dict[str, list[dict]],
    ood_split: dict[str, list[dict]],
) -> dict:
    """Describe training evidence without copying the rollout dataset itself."""
    inputs = []
    total_records = 0
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        rows = load_jsonl_records([path])
        total_records += len(rows)
        inputs.append(
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "records": len(rows),
                "root_groups": _root_group_count(rows),
                "sources": sorted({str(row.get("source", "")) for row in rows}),
            }
        )

    return {
        "format": DATASET_MANIFEST_FORMAT,
        "rollout_schema_version": ROLLOUT_SCHEMA_VERSION,
        "feature_schema_id": FEATURE_SCHEMA_ID,
        "target_schema_id": TARGET_SCHEMA_ID,
        "root_group_key": ["source", "generation"],
        "inputs": inputs,
        "total_records": total_records,
        "model_selection_split": {
            "strategy": "grouped-hazard-stratified-v1",
            "partitions": {
                name: summarize_partition(rows)
                for name, rows in model_split.items()
            },
        },
        "ood_stress_split": {
            "strategy": "grouped-chronological-v1",
            "partitions": {
                name: summarize_partition(rows)
                for name, rows in ood_split.items()
            },
        },
    }


def build_metadata(
    *,
    model_id: str,
    hidden_size: int,
    epochs: int,
    learning_rate: float,
    seed: int,
    training_script: str,
    training_script_version: str,
    repo_root: Path | None = None,
    note: str | None = None,
    tags: Iterable[str] = (),
) -> dict:
    provenance = git_provenance(repo_root)
    return {
        "format": ARTIFACT_FORMAT,
        "model_id": str(model_id),
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_format": MODEL_FORMAT,
        "feature_schema_id": FEATURE_SCHEMA_ID,
        "output_schema": list(OUTPUT_SCHEMA),
        "training_script": str(training_script),
        "training_script_version": str(training_script_version),
        "hyperparameters": {
            "hidden_size": int(hidden_size),
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
            "seed": int(seed),
        },
        "split_strategy": {
            "model_selection": "grouped-hazard-stratified-v1",
            "ood_stress": "grouped-chronological-v1",
        },
        "authority_boundary": AUTHORITY_BOUNDARY,
        "experiment_tags": [str(tag) for tag in tags],
        "note": None if note is None else str(note),
        **provenance,
    }


def build_metrics(
    *,
    train: dict,
    validation: dict,
    test: dict,
    chronological_test: dict,
    training_seconds: float,
    inference_latency_us_per_record: float | None = None,
) -> dict:
    return {
        "format": METRICS_FORMAT,
        "model_selection": {
            "train": train,
            "validation": validation,
            "test": test,
        },
        "ood_stress_test": {
            "chronological_test": chronological_test,
        },
        "training_seconds": float(training_seconds),
        "inference_latency_us_per_record": (
            None
            if inference_latency_us_per_record is None
            else float(inference_latency_us_per_record)
        ),
    }


def write_model_artifact(
    artifact_dir: Path,
    *,
    model: TinySurrogateMLP,
    metadata: dict,
    metrics: dict,
    dataset_manifest: dict,
) -> Path:
    """Persist one complete inference artifact directory without overwriting."""
    artifact_dir = Path(artifact_dir).expanduser().resolve()
    artifact_dir.mkdir(parents=True, exist_ok=False)
    model.save_json(artifact_dir / "model.json")
    _write_json(artifact_dir / "metadata.json", metadata)
    _write_json(artifact_dir / "metrics.json", metrics)
    _write_json(artifact_dir / "dataset_manifest.json", dataset_manifest)
    return artifact_dir


def load_model_artifact(
    artifact_dir: Path,
    *,
    expected_feature_schema_id: str = FEATURE_SCHEMA_ID,
) -> tuple[TinySurrogateMLP, dict, dict, dict]:
    """Load and cross-check a complete versioned artifact directory."""
    artifact_dir = Path(artifact_dir).expanduser().resolve()
    missing = [name for name in _REQUIRED_FILES if not (artifact_dir / name).is_file()]
    if missing:
        raise ValueError(
            f"incomplete surrogate artifact {artifact_dir}: missing {', '.join(missing)}"
        )

    metadata = json.loads((artifact_dir / "metadata.json").read_text(encoding="utf-8"))
    metrics = json.loads((artifact_dir / "metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((artifact_dir / "dataset_manifest.json").read_text(encoding="utf-8"))

    if metadata.get("format") != ARTIFACT_FORMAT:
        raise ValueError(f"unsupported surrogate artifact format: {metadata.get('format')!r}")
    if metrics.get("format") != METRICS_FORMAT:
        raise ValueError(f"unsupported surrogate metrics format: {metrics.get('format')!r}")
    if manifest.get("format") != DATASET_MANIFEST_FORMAT:
        raise ValueError(
            f"unsupported surrogate dataset manifest format: {manifest.get('format')!r}"
        )

    for name, payload in (("metadata", metadata), ("dataset manifest", manifest)):
        actual = payload.get("feature_schema_id")
        if actual != expected_feature_schema_id:
            raise ValueError(
                f"{name} feature schema mismatch: expected {expected_feature_schema_id!r}, "
                f"got {actual!r}"
            )

    if tuple(metadata.get("output_schema", ())) != OUTPUT_SCHEMA:
        raise ValueError(
            f"metadata output schema mismatch: expected {OUTPUT_SCHEMA!r}, "
            f"got {metadata.get('output_schema')!r}"
        )

    model = TinySurrogateMLP.load_json(
        artifact_dir / "model.json",
        expected_feature_schema_id=expected_feature_schema_id,
    )
    return model, metadata, metrics, manifest
