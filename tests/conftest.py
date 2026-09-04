from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

GIT_ENV = [
    "-c",
    "user.name=test",
    "-c",
    "user.email=test@example.com",
]


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *GIT_ENV, *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    return tmp_path


def commit_all(repo: Path, message: str = "commit") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
