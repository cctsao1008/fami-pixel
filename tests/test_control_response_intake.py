from pathlib import Path

from fami_pixel.control.response_intake import read_available_responses


def test_response_intake_preserves_path_order_and_skips_missing(tmp_path):
    paths = [tmp_path / "r0.json", tmp_path / "r1.json", tmp_path / "r2.json"]
    payloads = {
        paths[0]: {"worker": 0, "generation": 4},
        paths[2]: {"worker": 2, "generation": 4},
    }

    def reader(path: Path):
        return payloads.get(path)

    assert read_available_responses(paths, reader=reader) == [
        {"worker": 0, "generation": 4},
        {"worker": 2, "generation": 4},
    ]


def test_response_intake_returns_copies(tmp_path):
    path = tmp_path / "r0.json"
    original = {"worker": 0, "branch_proofs": []}

    responses = read_available_responses([path], reader=lambda _: original)
    assert responses == [original]
    assert responses[0] is not original

    responses[0]["worker"] = 9
    assert original["worker"] == 0


def test_response_intake_ignores_non_mapping_payloads(tmp_path):
    paths = [tmp_path / "r0.json", tmp_path / "r1.json"]
    values = iter(([1, 2, 3], {"worker": 1}))

    responses = read_available_responses(paths, reader=lambda _: next(values))
    assert responses == [{"worker": 1}]
