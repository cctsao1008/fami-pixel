import importlib.util
import json
from pathlib import Path
import sys


def _load_trainer():
    root = Path(__file__).resolve().parents[1]
    path = root / "tools" / "train_smb1_surrogate_mlp.py"
    spec = importlib.util.spec_from_file_location("train_smb1_surrogate_artifact", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(generation: int, candidate: str, delta_x: int, *, risk: bool = False) -> dict:
    return {
        "schema": 1,
        "source": "trainer-test",
        "generation": generation,
        "candidate": {
            "name": candidate,
            "horizon_frames": 8,
            "schedule": [{"buttons": 0x82 if candidate == "fast" else 0x80, "frames": 8}],
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
            "terminal": "death" if risk else "none",
            "death": risk,
            "doomed_within_probe": False,
            "no_progress": delta_x <= 0,
        },
    }


def test_training_emits_auto_versioned_artifact_directory(tmp_path, monkeypatch):
    trainer = _load_trainer()
    rows = []
    for generation in range(9):
        rows.append(_row(generation, "slow", 2))
        rows.append(_row(generation, "fast", 10, risk=(generation in {2, 7})))

    dataset = tmp_path / "rollouts.jsonl"
    dataset.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "models"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_smb1_surrogate_mlp.py",
            str(dataset),
            "--hidden",
            "4",
            "--epochs",
            "1",
            "--progress-every",
            "0",
            "--output-root",
            str(output_root),
            "--tag",
            "pytest",
        ],
    )

    assert trainer.main() == 0
    artifact = output_root / "smb1-surrogate-v001"
    assert artifact.is_dir()
    assert {path.name for path in artifact.iterdir()} == {
        "model.json",
        "metadata.json",
        "metrics.json",
        "dataset_manifest.json",
    }

    model = json.loads((artifact / "model.json").read_text(encoding="utf-8"))
    metadata = json.loads((artifact / "metadata.json").read_text(encoding="utf-8"))
    metrics = json.loads((artifact / "metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((artifact / "dataset_manifest.json").read_text(encoding="utf-8"))

    assert model["feature_schema_id"] == "smb1-tiny-surrogate-features-v1"
    assert metadata["model_id"] == "smb1-surrogate-v001"
    assert metadata["experiment_tags"] == ["pytest"]
    assert metrics["model_selection"]["train"]["records"] > 0
    assert metrics["ood_stress_test"]["chronological_test"]["records"] > 0
    assert manifest["inputs"][0]["records"] == len(rows)
    assert len(manifest["inputs"][0]["sha256"]) == 64
