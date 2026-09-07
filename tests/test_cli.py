from __future__ import annotations

import json
from pathlib import Path

import pytest

from changelens.cli import main
from changelens.gate import METRIC_NAMES
from tests.conftest import commit_all, git, write_tree


@pytest.fixture
def project(git_repo: Path) -> Path:
    write_tree(
        git_repo,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "def run(n):\n    return n + 1\n",
            "app.py": "from pkg.core import run\n\n\ndef go():\n    return run(1)\n",
            "tests/test_app.py": "from app import go\n",
        },
    )
    commit_all(git_repo, "baseline")
    write_tree(git_repo, {"pkg/core.py": "def run(n):\n    return n + 2\n"})
    commit_all(git_repo, "change")
    return git_repo


def test_ref_mode(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["HEAD~1", "--repo", str(project)]) == 0
    out = capsys.readouterr().out
    assert "pkg/core.py::run" in out
    assert "app.py  (calls run)" in out
    assert "tests/test_app.py" in out
    assert "Confidence: High" in out


def test_staged_mode(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_tree(project, {"pkg/core.py": "def run(n):\n    return n * 3\n"})
    git(project, "add", "-A")
    assert main(["--staged", "--repo", str(project)]) == 0
    assert "pkg/core.py::run" in capsys.readouterr().out


def test_json_mode(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["HEAD~1", "--json", "--repo", str(project)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"][0]["symbol"] == "run"


def test_mermaid_mode(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["HEAD~1", "--mermaid", "--repo", str(project)]) == 0
    assert capsys.readouterr().out.startswith("flowchart TD")


def test_no_args_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "pass a ref" in capsys.readouterr().err


def test_ref_and_staged_conflict(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["HEAD~1", "--staged"]) == 2
    assert "not both" in capsys.readouterr().err


def test_bad_ref_clean_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["no-such-ref", "--repo", str(project)]) == 2
    assert "error:" in capsys.readouterr().err


def test_not_a_repo_clean_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["HEAD~1", "--repo", str(tmp_path)]) == 2
    assert "error:" in capsys.readouterr().err


def test_empty_range(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["HEAD", "--repo", str(project)]) == 0
    assert "No changes found" in capsys.readouterr().out


def test_empty_range_still_emits_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A CI script piping into jq should not have to special-case a sentence.
    assert main(["HEAD", "--json", "--repo", str(project)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] == []
    assert payload["metrics"]["affected"] == 0


class TestTestsOnly:
    def test_stdout_is_the_test_paths_and_nothing_else(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD~1", "--tests-only", "--repo", str(project)]) == 0
        captured = capsys.readouterr()
        assert captured.out == "tests/test_app.py\n"
        assert captured.err == ""

    def test_it_is_pipeable_line_by_line(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["HEAD~1", "--tests-only", "--repo", str(project)])
        lines = capsys.readouterr().out.splitlines()
        assert lines == ["tests/test_app.py"]
        assert all(Path(project / line).exists() for line in lines)

    def test_no_relevant_tests_prints_nothing_to_stdout(
        self, git_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # An empty stdout is a real answer here, so the sentence explaining it
        # goes to stderr where a test runner will not read it as a path.
        write_tree(git_repo, {"pkg/__init__.py": "", "pkg/core.py": "def run():\n    return 1\n"})
        commit_all(git_repo, "baseline")
        write_tree(git_repo, {"pkg/core.py": "def run():\n    return 2\n"})
        commit_all(git_repo, "change")
        assert main(["HEAD~1", "--tests-only", "--repo", str(git_repo)]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "No relevant tests found" in captured.err

    def test_an_empty_range_says_so_on_stderr_too(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD", "--tests-only", "--repo", str(project)]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "No relevant tests found" in captured.err

    def test_a_gate_keeps_stdout_clean_and_still_sets_the_exit_code(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["HEAD~1", "--tests-only", "--fail-on", "tests>0", "--repo", str(project)])
        captured = capsys.readouterr()
        assert code == 1
        assert captured.out == "tests/test_app.py\n"
        assert "Gate: failed" in captured.err

    @pytest.mark.parametrize("other", ["--json", "--mermaid"])
    def test_it_conflicts_with_the_other_output_formats(
        self, other: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD~1", other, "--tests-only"]) == 2
        err = capsys.readouterr().err
        assert "not several" in err
        assert "--tests-only" in err and other in err

    def test_json_and_mermaid_still_conflict_with_each_other(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD~1", "--json", "--mermaid"]) == 2
        assert "not several" in capsys.readouterr().err


class TestFailOn:
    def test_tripped_gate_exits_one(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD~1", "--fail-on", "affected>1", "--repo", str(project)]) == 1
        out = capsys.readouterr().out
        assert "Gate: failed" in out
        assert "FAIL  affected>1  (actual: affected = 2)" in out

    def test_passing_gate_exits_zero(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD~1", "--fail-on", "affected>50", "--repo", str(project)]) == 0
        assert "Gate: passed" in capsys.readouterr().out

    def test_repeated_flags_are_all_evaluated(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            ["HEAD~1", "--fail-on", "affected>50", "--fail-on", "tests=1", "--repo", str(project)]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "ok    affected>50" in out
        assert "FAIL  tests=1" in out

    def test_confidence_gate(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["HEAD~1", "--fail-on", "confidence>=medium", "--repo", str(project)]) == 0
        assert "actual: confidence = high" in capsys.readouterr().out

    def test_gate_is_absent_without_the_flag(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD~1", "--repo", str(project)]) == 0
        assert "Gate:" not in capsys.readouterr().out

    def test_help_lists_every_metric(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(["--help"])
        help_text = capsys.readouterr().out
        for metric in METRIC_NAMES:
            assert f"{metric}:" in help_text

    def test_bad_expression_is_a_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Rejected before git runs, so no --repo is needed to reach it.
        assert main(["HEAD~1", "--fail-on", "affected>lots"]) == 2
        assert "whole number" in capsys.readouterr().err

    def test_json_carries_the_gate_and_keeps_stdout_clean(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["HEAD~1", "--json", "--fail-on", "tests>0", "--repo", str(project)])
        captured = capsys.readouterr()
        assert code == 1
        payload = json.loads(captured.out)
        assert payload["gate"]["failed"] is True
        assert payload["gate"]["conditions"][0] == {
            "expression": "tests>0",
            "metric": "tests",
            "operator": ">",
            "value": "0",
            "actual": 1,
            "tripped": True,
        }
        assert "Gate: failed" in captured.err

    def test_mermaid_keeps_stdout_clean(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["HEAD~1", "--mermaid", "--fail-on", "tests>0", "--repo", str(project)])
        captured = capsys.readouterr()
        assert code == 1
        assert "Gate:" not in captured.out
        assert "Gate: failed" in captured.err

    def test_non_python_change_skips_the_gate(
        self, git_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_tree(git_repo, {"README.md": "hello\n"})
        commit_all(git_repo, "baseline")
        write_tree(git_repo, {"README.md": "hello there\n"})
        commit_all(git_repo, "docs")
        assert main(["HEAD~1", "--fail-on", "tests=0", "--repo", str(git_repo)]) == 0
        assert "Gate: skipped" in capsys.readouterr().out

    def test_empty_range_skips_the_gate(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD", "--fail-on", "tests=0", "--repo", str(project)]) == 0
        out = capsys.readouterr().out
        assert "No changes found" in out
        assert "Gate: skipped" in out


@pytest.fixture
def branch(git_repo: Path) -> Path:
    """A repo whose branch grows: one commit off `base`, then a wider one.

    pkg/core.py reaches app.py and its test; pkg/util.py reaches a second,
    disjoint pair. Changing core alone is the baseline; the later commit
    reaches into util and widens the radius by two files.
    """
    write_tree(
        git_repo,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "def run(n):\n    return n + 1\n",
            "pkg/util.py": "def helper(n):\n    return n * 2\n",
            "app.py": "from pkg.core import run\n\n\ndef go():\n    return run(1)\n",
            "lib.py": "from pkg.util import helper\n\n\ndef use():\n    return helper(2)\n",
            "tests/test_app.py": "from app import go\n",
            "tests/test_lib.py": "from lib import use\n",
        },
    )
    commit_all(git_repo, "baseline")
    git(git_repo, "branch", "base")
    write_tree(git_repo, {"pkg/core.py": "def run(n):\n    return n + 2\n"})
    commit_all(git_repo, "narrow change")
    return git_repo


def grow(repo: Path) -> None:
    write_tree(repo, {"pkg/util.py": "def helper(n):\n    return n * 3\n"})
    commit_all(repo, "wider change")


class TestBaseline:
    def test_save_then_gate_on_growth(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        saved = capsys.readouterr().out
        assert "Baseline saved to .changelens-baseline.json (ref base: affected = 2" in saved
        assert (branch / ".changelens-baseline.json").exists()

        grow(branch)
        assert main(["base", "--fail-on", "affected>baseline", "--repo", str(branch)]) == 1
        out = capsys.readouterr().out
        assert "FAIL  affected>baseline  (actual: affected = 4, baseline 2, +2)" in out
        assert "2 files entered the radius since the baseline:" in out
        assert "          lib.py" in out
        assert "          tests/test_lib.py" in out

    def test_growth_within_the_offset_passes(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        grow(branch)
        assert main(["base", "--fail-on", "affected>baseline+5", "--repo", str(branch)]) == 0
        assert "Gate: passed" in capsys.readouterr().out

    def test_an_unchanged_branch_does_not_trip(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        assert main(["base", "--fail-on", "affected>baseline", "--repo", str(branch)]) == 0
        assert "baseline 2, no change" in capsys.readouterr().out

    def test_missing_baseline_is_an_error_with_the_command_to_fix_it(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Never a silent pass: that is the failure mode a gate exists to stop.
        assert main(["base", "--fail-on", "affected>baseline", "--repo", str(branch)]) == 2
        err = capsys.readouterr().err
        assert "no baseline at .changelens-baseline.json" in err
        assert "changelens base --save-baseline" in err

    def test_a_baseline_for_another_range_is_refused(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        capsys.readouterr()
        code = main(["HEAD~1", "--fail-on", "affected>baseline", "--repo", str(branch)])
        err = capsys.readouterr().err
        assert code == 2
        assert "taken against ref base and this run analyzes ref HEAD~1" in err
        assert "not comparable" in err

    def test_a_corrupt_baseline_is_an_error(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (branch / ".changelens-baseline.json").write_text("{", encoding="utf-8")
        assert main(["base", "--fail-on", "affected>baseline", "--repo", str(branch)]) == 2
        assert "not valid JSON" in capsys.readouterr().err

    def test_a_baseline_that_cannot_answer_the_condition_is_an_error(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Hand-written, or written by a version that counted something else.
        (branch / ".changelens-baseline.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "spec": {"ref": "base", "staged": False},
                    "confidence": "High",
                    "metrics": {"affected": 2},
                }
            ),
            encoding="utf-8",
        )
        assert main(["base", "--fail-on", "tests>baseline", "--repo", str(branch)]) == 2
        err = capsys.readouterr().err
        assert "no 'tests' metric" in err
        assert "--save-baseline" in err

    def test_a_custom_path_is_written_and_read(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        where = branch / "ci" / "radius.json"
        assert (
            main(["base", "--save-baseline", "--baseline", str(where), "--repo", str(branch)]) == 0
        )
        assert "Baseline saved to ci/radius.json" in capsys.readouterr().out
        grow(branch)
        code = main(
            [
                "base",
                "--fail-on",
                "affected>baseline",
                "--baseline",
                str(where),
                "--repo",
                str(branch),
            ]
        )
        assert code == 1
        assert "baseline 2, +2" in capsys.readouterr().out

    def test_baseline_path_without_a_reader_or_writer_is_a_usage_error(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # --baseline alone reads as "gate against this", and it does not gate.
        code = main(
            ["base", "--baseline", "x.json", "--fail-on", "affected>1", "--repo", str(branch)]
        )
        assert code == 2
        assert "nothing here does either" in capsys.readouterr().err

    def test_a_docs_only_range_does_not_overwrite_the_baseline(
        self, git_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # All-zero counts saved here would make the next branch look like it
        # invented the entire radius.
        write_tree(git_repo, {"README.md": "hello\n", "pkg/__init__.py": ""})
        commit_all(git_repo, "baseline")
        write_tree(git_repo, {"README.md": "hello there\n"})
        commit_all(git_repo, "docs")
        kept = git_repo / ".changelens-baseline.json"
        kept.write_text("sentinel", encoding="utf-8")
        assert main(["HEAD~1", "--save-baseline", "--repo", str(git_repo)]) == 0
        assert "Baseline not saved" in capsys.readouterr().out
        assert kept.read_text(encoding="utf-8") == "sentinel"

    def test_saving_and_gating_in_one_run(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A failing gate does not stop the baseline being recorded: the run
        # still measured the branch.
        code = main(["base", "--fail-on", "affected>1", "--save-baseline", "--repo", str(branch)])
        assert code == 1
        out = capsys.readouterr().out
        assert "Gate: failed" in out
        assert "Baseline saved" in out
        assert (branch / ".changelens-baseline.json").exists()

    def test_an_unwritable_baseline_is_an_error_after_the_report(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        blocker = branch / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        code = main(
            [
                "base",
                "--save-baseline",
                "--baseline",
                str(blocker / "b.json"),
                "--repo",
                str(branch),
            ]
        )
        captured = capsys.readouterr()
        assert code == 2
        assert "Change blast radius" in captured.out
        assert "could not write the baseline" in captured.err

    def test_staged_baselines_round_trip(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_tree(branch, {"pkg/core.py": "def run(n):\n    return n + 7\n"})
        git(branch, "add", "-A")
        assert main(["--staged", "--save-baseline", "--repo", str(branch)]) == 0
        assert "staged changes: affected = 2" in capsys.readouterr().out
        assert main(["--staged", "--fail-on", "affected>baseline", "--repo", str(branch)]) == 0
        assert "baseline 2, no change" in capsys.readouterr().out

    def test_json_carries_the_baseline_and_keeps_stdout_clean(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        capsys.readouterr()
        grow(branch)
        code = main(["base", "--json", "--fail-on", "affected>baseline", "--repo", str(branch)])
        captured = capsys.readouterr()
        assert code == 1
        payload = json.loads(captured.out)
        assert payload["schema_version"] == 5
        assert payload["gate"]["baseline"]["spec"] == {"ref": "base", "staged": False}
        assert payload["gate"]["baseline"]["metrics"]["affected"] == 2
        condition = payload["gate"]["conditions"][0]
        assert (condition["baseline"], condition["delta"]) == (2, 2)
        assert condition["entered"] == ["lib.py", "tests/test_lib.py"]
        assert "Gate: failed" in captured.err

    def test_a_constant_gate_emits_exactly_what_it_always_did(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--json", "--fail-on", "affected>1", "--repo", str(branch)]) == 1
        condition = json.loads(capsys.readouterr().out)["gate"]["conditions"][0]
        assert set(condition) == {"expression", "metric", "operator", "value", "actual", "tripped"}

    def test_a_percentage_threshold_trips_end_to_end(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Baseline of 2 affected files; +25% of 2 is 0.5, so a threshold of
        # 2.5, and the grown branch's 4 files clear it.
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        capsys.readouterr()
        grow(branch)
        code = main(["base", "--fail-on", "affected>baseline+25%", "--repo", str(branch)])
        out = capsys.readouterr().out
        assert code == 1
        assert "FAIL  affected>baseline+25%" in out
        assert "(actual: affected = 4, baseline 2, +2, threshold 2.5)" in out

    def test_a_percentage_threshold_wide_enough_to_absorb_the_growth_passes(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        grow(branch)
        code = main(["base", "--fail-on", "affected>baseline+100%", "--repo", str(branch)])
        out = capsys.readouterr().out
        assert code == 0
        assert "Gate: passed" in out
        assert "ok    affected>baseline+100%" in out
        assert "threshold 4)" in out

    def test_a_bad_percentage_is_a_usage_error_before_git_runs(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["HEAD~1", "--fail-on", "affected>baseline+2.5%"]) == 2
        assert "cannot read the baseline offset" in capsys.readouterr().err

    def test_json_carries_the_threshold_a_percentage_worked_out_to(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        capsys.readouterr()
        grow(branch)
        code = main(["base", "--json", "--fail-on", "affected>baseline+25%", "--repo", str(branch)])
        assert code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 5
        condition = payload["gate"]["conditions"][0]
        assert condition["value"] == "baseline+25%"
        assert condition["threshold"] == "2.5"

    def test_a_baseline_from_this_history_says_nothing(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        capsys.readouterr()
        grow(branch)
        assert main(["base", "--fail-on", "affected>baseline", "--repo", str(branch)]) == 1
        out = capsys.readouterr().out
        assert "Gate: failed\n" in out
        assert "Warning" not in out

    def test_the_save_note_goes_to_stderr_in_json_mode(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--json", "--save-baseline", "--repo", str(branch)]) == 0
        captured = capsys.readouterr()
        json.loads(captured.out)  # stdout stays parseable
        assert "Baseline saved" in captured.err


def flat(text: str) -> str:
    """Rendered text with its line wrapping collapsed, for asserting sentences."""
    return " ".join(text.split())


def diverge(repo: Path) -> None:
    """Save a baseline on a side branch, then go back and grow a different one.

    The result is the failure this check exists for: a baseline that is real,
    readable, and taken against the same ref, but recorded at a commit this
    run's HEAD does not descend from.
    """
    git(repo, "checkout", "-q", "-b", "sidequest")
    write_tree(repo, {"pkg/core.py": "def run(n):\n    return n + 99\n"})
    commit_all(repo, "side change")
    assert main(["base", "--save-baseline", "--repo", str(repo)]) == 0
    git(repo, "checkout", "-q", "-")
    grow(repo)


class TestBaselineProvenance:
    def test_a_baseline_from_divergent_history_warns_and_labels_the_verdict(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        diverge(branch)
        capsys.readouterr()
        code = main(["base", "--fail-on", "affected>baseline+50", "--repo", str(branch)])
        out = flat(capsys.readouterr().out)
        # The gate itself passes, which is exactly why the warning matters:
        # without it this reads as a clean branch.
        assert code == 0
        assert "Gate: passed (baseline is not from this history)" in out
        assert "which is not an ancestor of this run's HEAD" in out
        assert "--require-baseline-ancestor" in out

    def test_the_strict_flag_refuses_instead(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        diverge(branch)
        capsys.readouterr()
        code = main(
            [
                "base",
                "--fail-on",
                "affected>baseline",
                "--require-baseline-ancestor",
                "--repo",
                str(branch),
            ]
        )
        err = flat(capsys.readouterr().err)
        assert code == 2
        assert "not an ancestor of this run's HEAD" in err
        assert "--require-baseline-ancestor was passed" in err
        assert "Re-save the baseline at .changelens-baseline.json" in err

    def test_a_commit_that_is_not_in_the_repository_is_a_different_message(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A shallow clone, a force-push, or a rebase, not a sibling branch.
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        path = branch / ".changelens-baseline.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["head"] = "0" * 40
        path.write_text(json.dumps(payload), encoding="utf-8")
        capsys.readouterr()
        assert main(["base", "--fail-on", "affected>baseline", "--repo", str(branch)]) == 0
        out = flat(capsys.readouterr().out)
        assert "Gate: passed (baseline provenance unverified)" in out
        assert "which is not in this repository at all" in out
        assert "not an ancestor" not in out

    def test_a_baseline_with_no_recorded_commit_still_says_so(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        path = branch / ".changelens-baseline.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["head"] = None
        path.write_text(json.dumps(payload), encoding="utf-8")
        capsys.readouterr()
        assert main(["base", "--fail-on", "affected>baseline", "--repo", str(branch)]) == 0
        assert "the baseline records no commit" in flat(capsys.readouterr().out)

    def test_the_strict_flag_is_quiet_when_the_baseline_checks_out(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["base", "--save-baseline", "--repo", str(branch)]) == 0
        capsys.readouterr()
        grow(branch)
        code = main(
            [
                "base",
                "--fail-on",
                "affected>baseline+5",
                "--require-baseline-ancestor",
                "--repo",
                str(branch),
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "Gate: passed\n" in out
        assert "Warning" not in out

    def test_the_strict_flag_without_a_baseline_condition_is_a_usage_error(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Otherwise it reads in a pipeline as a guarantee nobody is making.
        code = main(
            [
                "base",
                "--fail-on",
                "affected>1",
                "--require-baseline-ancestor",
                "--repo",
                str(branch),
            ]
        )
        assert code == 2
        assert "no condition here compares against one" in capsys.readouterr().err

    def test_json_carries_the_provenance(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        diverge(branch)
        capsys.readouterr()
        code = main(["base", "--json", "--fail-on", "affected>baseline", "--repo", str(branch)])
        payload = json.loads(capsys.readouterr().out)
        assert code in (0, 1)
        provenance = payload["gate"]["baseline"]["provenance"]
        assert provenance["status"] == "not_ancestor"
        assert provenance["verified"] is False
        assert provenance["head"] == git(branch, "rev-parse", "HEAD").strip()

    def test_the_warning_reaches_stderr_in_json_mode(
        self, branch: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # stdout stays machine-readable, so a human-facing warning that only
        # went there would be invisible in a piped pipeline.
        diverge(branch)
        capsys.readouterr()
        main(["base", "--json", "--fail-on", "affected>baseline", "--repo", str(branch)])
        captured = capsys.readouterr()
        json.loads(captured.out)
        assert "not an ancestor of this run's HEAD" in flat(captured.err)
