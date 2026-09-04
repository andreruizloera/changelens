from __future__ import annotations

import json
from pathlib import Path

import pytest

from changelens.cli import main
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
