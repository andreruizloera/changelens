from __future__ import annotations

import json
from pathlib import Path

import pytest

from changelens.baseline import (
    NO_HEAD,
    NOT_ANCESTOR,
    NOT_RECORDED,
    SCHEMA_VERSION,
    UNKNOWN_COMMIT,
    VERIFIED,
    Baseline,
    BaselineError,
    BaselineMissing,
    Provenance,
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


class TestProvenanceData:
    def test_only_an_ancestor_is_verified(self) -> None:
        assert Provenance(VERIFIED, "a" * 40, "b" * 40).ok
        for status in (NOT_ANCESTOR, UNKNOWN_COMMIT, NOT_RECORDED, NO_HEAD):
            assert not Provenance(status, "a" * 40, "b" * 40).ok

    def test_a_verified_baseline_needs_no_label(self) -> None:
        assert Provenance(VERIFIED, "a" * 40, "b" * 40).label is None

    def test_divergent_history_is_labeled_apart_from_unknown_provenance(self) -> None:
        # The two are different findings: one is a commit we located and
        # ruled out, the other is a commit we could not locate at all.
        assert Provenance(NOT_ANCESTOR).label == "baseline is not from this history"
        assert Provenance(UNKNOWN_COMMIT).label == "baseline provenance unverified"
        assert Provenance(NOT_RECORDED).label == "baseline provenance unverified"

    def test_each_status_describes_itself_differently(self) -> None:
        described = {
            Provenance(status, "a" * 40, "b" * 40).describe()
            for status in (NOT_ANCESTOR, UNKNOWN_COMMIT, NOT_RECORDED, NO_HEAD)
        }
        assert len(described) == 4

    def test_a_description_shortens_the_shas(self) -> None:
        text = Provenance(NOT_ANCESTOR, "a" * 40, "b" * 40).describe()
        assert "commit aaaaaaa," in text
        assert "(bbbbbbb)" in text
        assert "a" * 40 not in text

    def test_a_missing_commit_is_not_described_as_divergent_history(self) -> None:
        assert "shallow clone" in Provenance(UNKNOWN_COMMIT, "a" * 40, "b" * 40).describe()
        assert "not an ancestor" not in Provenance(UNKNOWN_COMMIT, "a" * 40, "b" * 40).describe()


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
