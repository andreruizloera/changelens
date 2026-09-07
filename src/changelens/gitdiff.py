"""Read and parse git diffs for the requested range.

Two modes:
- ref mode: `git diff -U0 <ref> HEAD` (what changed between a ref and HEAD)
- staged mode: `git diff -U0 --cached` (what is staged right now)

Changed line numbers are tracked on the new side of the diff. A pure
deletion hunk has no new-side lines, so we mark the line at the deletion
point as touched; the enclosing symbol at that point is what shrank.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from changelens.baseline import (
    NO_HEAD,
    NOT_ANCESTOR,
    NOT_RECORDED,
    UNKNOWN_COMMIT,
    VERIFIED,
    Baseline,
    Provenance,
)


class GitError(Exception):
    """A git invocation failed in an expected, reportable way."""


@dataclass
class FileDiff:
    path: str  # repo-relative posix path (new side, or old side when deleted)
    changed_lines: set[int] = field(default_factory=set)
    is_deleted: bool = False
    is_new: bool = False


def run_git(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:  # pragma: no cover
        raise GitError("git is not installed or not on PATH") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def repo_root(cwd: Path) -> Path:
    out = run_git(["rev-parse", "--show-toplevel"], cwd)
    return Path(out.strip())


def head_sha(root: Path) -> str | None:
    """The commit this run is analyzing, recorded in a baseline as provenance.

    None on a repository with no commits yet, which is not worth failing over.
    """
    try:
        return run_git(["rev-parse", "HEAD"], root).strip() or None
    except GitError:
        return None


def git_succeeds(args: list[str], cwd: Path) -> bool:
    """Whether a git command exited 0, for the ones that answer yes or no."""
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:  # pragma: no cover
        raise GitError("git is not installed or not on PATH") from exc
    return proc.returncode == 0


def commit_exists(root: Path, rev: str) -> bool:
    return git_succeeds(["cat-file", "-e", f"{rev}^{{commit}}"], root)


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Whether `ancestor` is reachable from `descendant`.

    `git merge-base --is-ancestor` answers with its exit code: 0 yes, 1 no.
    Callers check that both commits exist first, so anything nonzero here is
    the "no" and not a lookup failure wearing its clothes.
    """
    return git_succeeds(["merge-base", "--is-ancestor", ancestor, descendant], root)


def check_provenance(root: Path, baseline: Baseline) -> Provenance:
    """Where the baseline's recorded commit sits relative to this run's HEAD.

    A baseline from an unrelated branch, or from before a history rewrite,
    reads exactly like a real comparison and produces a verdict about
    nothing. This is the check that tells the two apart. It never raises: an
    unanswerable provenance question is itself one of the answers.
    """
    current = head_sha(root)
    if baseline.head is None:
        return Provenance(NOT_RECORDED, None, current)
    if current is None:
        return Provenance(NO_HEAD, baseline.head, None)
    if not commit_exists(root, baseline.head):
        return Provenance(UNKNOWN_COMMIT, baseline.head, current)
    if not is_ancestor(root, baseline.head, current):
        return Provenance(NOT_ANCESTOR, baseline.head, current)
    return Provenance(VERIFIED, baseline.head, current)


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(text: str) -> list[FileDiff]:
    diffs: list[FileDiff] = []
    current: FileDiff | None = None
    old_path: str | None = None
    for line in text.splitlines():
        if line.startswith("--- "):
            old_path = None if line == "--- /dev/null" else line[4:].removeprefix("a/")
        elif line.startswith("+++ "):
            if line == "+++ /dev/null":
                # Deleted file: keep the old path so we can still name the module.
                current = FileDiff(path=old_path or "", is_deleted=True)
            else:
                current = FileDiff(path=line[4:].removeprefix("b/"), is_new=old_path is None)
            diffs.append(current)
        elif current is not None and (m := _HUNK_RE.match(line)):
            new_start = int(m.group(3))
            new_count = int(m.group(4) if m.group(4) is not None else 1)
            if new_count > 0:
                current.changed_lines.update(range(new_start, new_start + new_count))
            else:
                # Pure deletion: mark the seam so the enclosing def is found.
                current.changed_lines.update({max(new_start, 1), new_start + 1})
    return [d for d in diffs if d.path]


def diff_against_ref(root: Path, ref: str) -> list[FileDiff]:
    # Validate the ref first for a clean error message.
    run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], root)
    return parse_unified_diff(run_git(["diff", "-U0", "--no-color", ref, "HEAD"], root))


def diff_staged(root: Path) -> list[FileDiff]:
    return parse_unified_diff(run_git(["diff", "-U0", "--no-color", "--cached"], root))


def file_content(root: Path, path: str, staged: bool) -> str | None:
    """Content of the analyzed side: index for --staged, HEAD otherwise."""
    spec = f":{path}" if staged else f"HEAD:{path}"
    try:
        return run_git(["show", spec], root)
    except GitError:
        return None
