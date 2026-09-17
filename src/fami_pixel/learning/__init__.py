"""Learning-side utilities for predictive models built from Mesen rollout data."""

from .model_artifact import (
    ARTIFACT_FORMAT,
    AUTHORITY_BOUNDARY,
    DATASET_MANIFEST_FORMAT,
    METRICS_FORMAT,
    TARGET_SCHEMA_ID,
    build_dataset_manifest,
    build_metadata,
    build_metrics,
    load_model_artifact,
    next_versioned_artifact_dir,
    sha256_file,
    write_model_artifact,
)
from .rollout_dataset import (
    ROLLOUT_SCHEMA_VERSION,
    build_rollout_record,
    load_jsonl_records,
    summarize_rollout_records,
    write_jsonl_record,
)
from .tiny_mlp import (
    FEATURE_SCHEMA_ID,
    FEATURE_VECTOR_SIZE,
    MODEL_FORMAT,
    OUTPUT_SCHEMA,
    TinySurrogateMLP,
)

__all__ = [
    "ARTIFACT_FORMAT",
    "AUTHORITY_BOUNDARY",
    "DATASET_MANIFEST_FORMAT",
    "FEATURE_SCHEMA_ID",
    "FEATURE_VECTOR_SIZE",
    "METRICS_FORMAT",
    "MODEL_FORMAT",
    "OUTPUT_SCHEMA",
    "ROLLOUT_SCHEMA_VERSION",
    "TARGET_SCHEMA_ID",
    "TinySurrogateMLP",
    "build_dataset_manifest",
    "build_metadata",
    "build_metrics",
    "build_rollout_record",
    "load_jsonl_records",
    "load_model_artifact",
    "next_versioned_artifact_dir",
    "sha256_file",
    "summarize_rollout_records",
    "write_jsonl_record",
    "write_model_artifact",
]
