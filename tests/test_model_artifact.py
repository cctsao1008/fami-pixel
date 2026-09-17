import json
from pathlib import Path

import pytest

from fami_pixel.learning.model_artifact import (
    build_dataset_manifest,
    build_metadata,
    build_metrics,
    load_model_artifact,
    next_versioned_artifact_dir,
    sha256_file,
    write_model_artifact,
)
from fami_pixel.learning.tiny_mlp import (
    FEATURE_SCHEMA_ID,
    FEATURE_VECTOR_SIZE,
    LEGACY_MODEL_FORMAT,
    TinySurrogateMLP,
    evaluate_model,
)


def _row(generation: int, candidate: str, delta_x: int) -> dict:
    return {
        "schema": 1,
        "source": "artifact-test",
        "generation": generation,
        "candidate": {
            "name": candidate,
            "horizon_frames": 8,
            "schedule": [{"buttons": 0x82, "frames": 8}],
        },
        "start": {
            "x": 100 + generation * 8,
            "y": 176,
            "y_high": 1,
            "vx": 20,
            "vy": 0,
            "player_state": 0,
            "engine": 8,
            "joypad": 0,
        },
        "target": {
            "delta_x": delta_x,
            "terminal": "none",
            "death": False,
            "doomed_within_probe": False,
            "no_progress": delta_x <= 0,
        },
    }


def _write_dataset(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_versioned_artifact_round_trip_preserves_model_and_provenance(tmp_path):
    rows = [_row(i, "run", 8 + i) for i in range(6)]
    dataset = tmp_path / "rollouts.jsonl"
    _write_dataset(dataset, rows)

    model_split = {
        "train": rows[:3],
        "validation": rows[3:4],
        "test": rows[4:],
    }
    ood_split = {
        "train": rows[:3],
        "validation": rows[3:4],
        "test": rows[4:],
    }
    model = TinySurrogateMLP(FEATURE_VECTOR_SIZE, hidden_size=5, seed=11)
    before = model.predict(rows[0])

    metadata = build_metadata(
        model_id="smb1-surrogate-v001",
        hidden_size=5,
        epochs=10,
        learning_rate=0.01,
        seed=11,
        training_script="tools/train_smb1_surrogate_mlp.py",
        training_script_version="test",
        repo_root=tmp_path,
        note="round-trip",
        tags=["unit-test"],
    )
    metrics = build_metrics(
        train=evaluate_model(model, model_split["train"]),
        validation=evaluate_model(model, model_split["validation"]),
        test=evaluate_model(model, model_split["test"]),
        chronological_test=evaluate_model(model, ood_split["test"]),
        training_seconds=1.25,
        inference_latency_us_per_record=12.5,
    )
    manifest = build_dataset_manifest(
        [dataset],
        model_split=model_split,
        ood_split=ood_split,
    )

    artifact = write_model_artifact(
        tmp_path / "smb1-surrogate-v001",
        model=model,
        metadata=metadata,
        metrics=metrics,
        dataset_manifest=manifest,
    )
    restored, restored_metadata, restored_metrics, restored_manifest = load_model_artifact(artifact)

    assert restored.predict(rows[0]) == before
    assert restored.feature_schema_id == FEATURE_SCHEMA_ID
    assert restored_metadata["model_id"] == "smb1-surrogate-v001"
    assert restored_metadata["authority_boundary"].startswith("learned model ranks/prunes")
    assert restored_metrics["model_selection"]["train"]["records"] == 3
    assert restored_manifest["inputs"][0]["records"] == 6
    assert restored_manifest["inputs"][0]["sha256"] == sha256_file(dataset)
    assert restored_manifest["root_group_key"] == ["source", "generation"]


def test_artifact_loader_rejects_incompatible_model_feature_schema(tmp_path):
    rows = [_row(i, "run", 8) for i in range(3)]
    dataset = tmp_path / "rollouts.jsonl"
    _write_dataset(dataset, rows)
    split = {"train": rows[:1], "validation": rows[1:2], "test": rows[2:]}
    model = TinySurrogateMLP(FEATURE_VECTOR_SIZE, hidden_size=4, seed=3)

    artifact = write_model_artifact(
        tmp_path / "smb1-surrogate-v001",
        model=model,
        metadata=build_metadata(
            model_id="smb1-surrogate-v001",
            hidden_size=4,
            epochs=1,
            learning_rate=0.01,
            seed=3,
            training_script="test",
            training_script_version="test",
            repo_root=tmp_path,
        ),
        metrics=build_metrics(
            train=evaluate_model(model, split["train"]),
            validation=evaluate_model(model, split["validation"]),
            test=evaluate_model(model, split["test"]),
            chronological_test=evaluate_model(model, split["test"]),
            training_seconds=0.0,
        ),
        dataset_manifest=build_dataset_manifest(
            [dataset],
            model_split=split,
            ood_split=split,
        ),
    )

    model_path = artifact / "model.json"
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    payload["feature_schema_id"] = "smb1-tiny-surrogate-features-v999"
    model_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="feature schema mismatch"):
        load_model_artifact(artifact)


def test_legacy_v1_model_remains_loadable_when_shape_and_outputs_match(tmp_path):
    model = TinySurrogateMLP(FEATURE_VECTOR_SIZE, hidden_size=4, seed=7)
    payload = model.to_dict()
    payload["format"] = LEGACY_MODEL_FORMAT
    payload.pop("feature_schema_id")
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = TinySurrogateMLP.load_json(path)
    assert restored.input_size == FEATURE_VECTOR_SIZE
    assert restored.feature_schema_id == FEATURE_SCHEMA_ID


def test_next_versioned_artifact_dir_advances_without_overwrite(tmp_path):
    assert next_versioned_artifact_dir(tmp_path).name == "smb1-surrogate-v001"
    (tmp_path / "smb1-surrogate-v001").mkdir()
    (tmp_path / "smb1-surrogate-v004").mkdir()
    (tmp_path / "unrelated-v999").mkdir()
    assert next_versioned_artifact_dir(tmp_path).name == "smb1-surrogate-v005"
