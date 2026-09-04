from __future__ import annotations

from pathlib import Path

from changelens.gitdiff import diff_against_ref
from changelens.impact import HIGH, LOW, MEDIUM, analyze
from tests.conftest import commit_all, write_tree

BASE = {
    "pkg/__init__.py": "",
    "pkg/core.py": "def run(n):\n    return n + 1\n",
    "direct_caller.py": "from pkg.core import run\n\n\ndef go():\n    return run(1)\n",
    "direct_importer.py": "import pkg.core\n\nNAME = pkg.core.__name__\n",
    "indirect.py": "from direct_caller import go\n\n\ndef top():\n    return go()\n",
    "unrelated.py": "VALUE = 42\n",
    "tests/test_core.py": "from pkg.core import run\n\n\ndef test_run():\n    assert run(1) == 2\n",
    "tests/test_indirect.py": (
        "from indirect import top\n\n\ndef test_top():\n    assert top() == 2\n"
    ),
}

CHANGED_CORE = {"pkg/core.py": "def run(n):\n    return n + 2\n"}


def make_change(repo: Path, files: dict[str, str]) -> None:
    write_tree(repo, BASE)
    commit_all(repo, "baseline")
    write_tree(repo, files)
    commit_all(repo, "change")


def run_analysis(repo: Path):
    return analyze(repo, diff_against_ref(repo, "HEAD~1"))


def test_changed_symbol_detected(git_repo: Path) -> None:
    make_change(git_repo, CHANGED_CORE)
    report = run_analysis(git_repo)
    assert [(c.file, c.qualname) for c in report.changed] == [("pkg/core.py", "run")]


def test_direct_dependents_found_and_ranked(git_repo: Path) -> None:
    make_change(git_repo, CHANGED_CORE)
    report = run_analysis(git_repo)
    files = [d.file for d in report.direct_dependents]
    # Caller of the changed symbol ranks above the mere importer.
    assert files == ["direct_caller.py", "direct_importer.py"]
    assert report.direct_dependents[0].calls_changed_symbol
    assert report.direct_dependents[0].called_symbols == ("run",)
    assert not report.direct_dependents[1].calls_changed_symbol


def test_transitive_dependents_and_unrelated_excluded(git_repo: Path) -> None:
    make_change(git_repo, CHANGED_CORE)
    report = run_analysis(git_repo)
    affected = [d.file for d in report.potentially_affected]
    assert affected == ["indirect.py"]
    assert report.potentially_affected[0].distance == 2
    all_files = affected + [d.file for d in report.direct_dependents]
    assert "unrelated.py" not in all_files


def test_relevant_tests_direct_and_transitive(git_repo: Path) -> None:
    make_change(git_repo, CHANGED_CORE)
    report = run_analysis(git_repo)
    assert [d.file for d in report.relevant_tests] == [
        "tests/test_core.py",
        "tests/test_indirect.py",
    ]


def test_module_level_change_reported_as_module(git_repo: Path) -> None:
    make_change(git_repo, {"pkg/core.py": "LIMIT = 9\n\n\ndef run(n):\n    return n + 1\n"})
    report = run_analysis(git_repo)
    assert ("pkg/core.py", None) in [(c.file, c.qualname) for c in report.changed]


def test_deleted_module_dependents_found(git_repo: Path) -> None:
    write_tree(git_repo, BASE)
    commit_all(git_repo, "baseline")
    (git_repo / "pkg/core.py").unlink()
    commit_all(git_repo, "delete core")
    report = run_analysis(git_repo)
    assert [(c.file, c.kind) for c in report.changed] == [("pkg/core.py", "module")]
    dependents = {d.file for d in report.direct_dependents}
    assert {"direct_caller.py", "direct_importer.py"} <= dependents
    assert "tests/test_core.py" in {d.file for d in report.relevant_tests}


def test_confidence_high_on_clean_repo(git_repo: Path) -> None:
    make_change(git_repo, CHANGED_CORE)
    report = run_analysis(git_repo)
    assert report.confidence == HIGH
    assert report.confidence_reasons == []


def test_confidence_medium_on_star_import_of_changed_module(git_repo: Path) -> None:
    write_tree(git_repo, BASE | {"wild.py": "from pkg.core import *\n"})
    commit_all(git_repo, "baseline")
    write_tree(git_repo, CHANGED_CORE)
    commit_all(git_repo, "change")
    report = run_analysis(git_repo)
    assert report.confidence == MEDIUM
    assert any("star-import" in r for r in report.confidence_reasons)
    # The star importer is still counted as a dependent.
    assert "wild.py" in {d.file for d in report.direct_dependents}


def test_confidence_medium_on_dynamic_import(git_repo: Path) -> None:
    write_tree(
        git_repo, BASE | {"loader.py": "import importlib\n\nm = importlib.import_module('x')\n"}
    )
    commit_all(git_repo, "baseline")
    write_tree(git_repo, CHANGED_CORE)
    commit_all(git_repo, "change")
    report = run_analysis(git_repo)
    assert report.confidence == MEDIUM
    assert any("dynamic imports" in r for r in report.confidence_reasons)


def test_confidence_medium_on_unparseable_bystander(git_repo: Path) -> None:
    write_tree(git_repo, BASE | {"broken.py": "def nope(:\n"})
    commit_all(git_repo, "baseline")
    write_tree(git_repo, CHANGED_CORE)
    commit_all(git_repo, "change")
    report = run_analysis(git_repo)
    assert report.confidence == MEDIUM
    assert any("could not be parsed" in r for r in report.confidence_reasons)


def test_confidence_low_on_unparseable_changed_file(git_repo: Path) -> None:
    make_change(git_repo, {"pkg/core.py": "def run(:\n"})
    report = run_analysis(git_repo)
    assert report.confidence == LOW
    assert any("pkg/core.py could not be parsed" in r for r in report.confidence_reasons)
    # Falls back to module-level attribution.
    assert [(c.file, c.qualname) for c in report.changed] == [("pkg/core.py", None)]


def test_non_python_changes_are_listed_not_analyzed(git_repo: Path) -> None:
    write_tree(git_repo, BASE | {"README.md": "hello\n"})
    commit_all(git_repo, "baseline")
    write_tree(git_repo, {"README.md": "hello world\n"} | CHANGED_CORE)
    commit_all(git_repo, "change")
    report = run_analysis(git_repo)
    assert report.ignored_files == ["README.md"]
    assert [c.qualname for c in report.changed] == ["run"]
