from __future__ import annotations

import json
from pathlib import Path

import pytest

from changelens.baseline import (
    SCHEMA_VERSION,
    Baseline,
    BaselineError,
    BaselineMissing,
    Spec,
    comparable,
    from_dict,
    load,
    save,
    to_dict,
)


def sample() -> Baseline:
    return Baseline(
        metrics={"changed": 1, "direct": 2, "transitive": 0, "tests": 2, "affected": 4},
        confidence="High",
        spec=Spec(ref="main"),
        files={"changed": ("pkg/core.py",), "direct": ("app.py", "cli.py")},
        head="0" * 40,
        created="2026-09-06T12:00:00+00:00",
        version="0.1.0",
    )


class TestRoundTrip:
    def test_save_then_load_is_the_same_baseline(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        save(path, sample())
        assert load(path) == sample()

    def test_the_file_is_readable_json(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        save(path, sample())
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["spec"] == {"ref": "main", "staged": False}
        assert payload["metrics"]["affected"] == 4
        assert payload["files"]["direct"] == ["app.py", "cli.py"]

    def test_save_creates_missing_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "ci" / "artifacts" / "baseline.json"
        save(path, sample())
        assert path.exists()

    def test_a_staged_spec_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        staged = Baseline(metrics={"affected": 1}, confidence="Low", spec=Spec(staged=True))
        save(path, staged)
        assert load(path).spec == Spec(ref=None, staged=True)

    def test_unwritable_path_is_a_baseline_error(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        with pytest.raises(BaselineError, match="could not write"):
            save(blocker / "baseline.json", sample())


class TestLoading:
    def test_missing_file_is_its_own_error(self, tmp_path: Path) -> None:
        # The CLI answers this one with "write a baseline first", so it has to
        # be distinguishable from a corrupt file.
        with pytest.raises(BaselineMissing):
            load(tmp_path / "nope.json")
        assert issubclass(BaselineMissing, BaselineError)

    def test_invalid_json_names_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(BaselineError, match="not valid JSON"):
            load(path)

    def test_wrong_schema_version_says_to_re_save(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        payload = to_dict(sample())
        payload["schema_version"] = SCHEMA_VERSION + 1
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(BaselineError, match="--save-baseline"):
            load(path)

    def test_a_json_list_is_not_a_baseline(self) -> None:
        with pytest.raises(BaselineError, match="expected a JSON object"):
            from_dict([1, 2, 3], "somewhere.json")

    @pytest.mark.parametrize(
        ("mutation", "message"),
        [
            ({"metrics": {}}, "no metrics"),
            ({"metrics": {"affected": "four"}}, "non-integer"),
            ({"metrics": {"affected": True}}, "non-integer"),
            ({"confidence": None}, "no confidence"),
            ({"spec": None}, "does not record what it was taken against"),
            ({"spec": {"ref": 7}}, "ref that is not a string"),
            ({"files": ["app.py"]}, "not an object"),
            ({"files": {"direct": [7]}}, "not paths"),
        ],
    )
    def test_corrupt_fields_are_rejected(self, mutation: dict, message: str) -> None:
        payload = to_dict(sample())
        payload.update(mutation)
        with pytest.raises(BaselineError, match=message):
            from_dict(payload, "somewhere.json")

    def test_files_are_optional(self) -> None:
        # A hand-written baseline with counts alone still gates; it just cannot
        # say which files are new.
        payload = to_dict(sample())
        del payload["files"]
        assert from_dict(payload, "somewhere.json").files == {}

    def test_provenance_that_is_not_a_string_is_dropped_not_fatal(self) -> None:
        # head, created, and version are for a human to read; a garbled one is
        # not a reason to refuse to gate.
        payload = to_dict(sample())
        payload.update({"head": 12, "created": [], "changelens_version": None})
        baseline = from_dict(payload, "somewhere.json")
        assert (baseline.head, baseline.created, baseline.version) == (None, None, None)
        assert baseline.metrics["affected"] == 4


class TestComparability:
    def test_same_ref_compares(self) -> None:
        assert comparable(sample(), Spec(ref="main"))

    def test_a_different_ref_does_not(self) -> None:
        # Two different diffs, so their numbers answer different questions.
        assert not comparable(sample(), Spec(ref="HEAD~1"))

    def test_staged_and_ref_are_different_questions(self) -> None:
        staged = Baseline(metrics={"affected": 1}, confidence="High", spec=Spec(staged=True))
        assert not comparable(staged, Spec(ref="main"))
        assert comparable(staged, Spec(staged=True))

    def test_describe_reads_as_a_sentence_fragment(self) -> None:
        assert Spec(ref="origin/main").describe() == "ref origin/main"
        assert Spec(staged=True).describe() == "staged changes"
