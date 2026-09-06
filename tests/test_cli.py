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
