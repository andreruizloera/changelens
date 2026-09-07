from __future__ import annotations

from pathlib import Path

from changelens.baseline import (
    NO_HEAD,
    NOT_ANCESTOR,
    NOT_RECORDED,
    UNKNOWN_COMMIT,
    VERIFIED,
    Baseline,
    Spec,
)
from changelens.gitdiff import (
    check_provenance,
    commit_exists,
    diff_against_ref,
    diff_staged,
    head_sha,
    is_ancestor,
    parse_unified_diff,
)
from tests.conftest import commit_all, git, write_tree

SAMPLE_DIFF = """\
diff --git a/pkg/mod.py b/pkg/mod.py
index 111..222 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -3,0 +4,2 @@ def f():
+    x = 1
+    y = 2
@@ -10,2 +12,0 @@ def g():
-    a = 1
-    b = 2
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,1 @@
+print("hi")
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,3 +0,0 @@
-a
-b
-c
"""


def test_parse_added_and_deleted_lines() -> None:
    diffs = parse_unified_diff(SAMPLE_DIFF)
    by_path = {d.path: d for d in diffs}
    mod = by_path["pkg/mod.py"]
    # Added lines 4-5, plus the deletion seam around new line 12.
    assert {4, 5} <= mod.changed_lines
    assert 12 in mod.changed_lines
    assert not mod.is_deleted


def test_parse_new_file() -> None:
    diffs = parse_unified_diff(SAMPLE_DIFF)
    new = next(d for d in diffs if d.path == "new.py")
    assert new.is_new
    assert new.changed_lines == {1}


def test_parse_deleted_file_keeps_old_path() -> None:
    diffs = parse_unified_diff(SAMPLE_DIFF)
    gone = next(d for d in diffs if d.is_deleted)
    assert gone.path == "gone.py"


def test_diff_against_ref_real_repo(git_repo: Path) -> None:
    write_tree(git_repo, {"a.py": "def f():\n    return 1\n"})
    commit_all(git_repo)
    write_tree(git_repo, {"a.py": "def f():\n    return 2\n"})
    commit_all(git_repo)
    diffs = diff_against_ref(git_repo, "HEAD~1")
    assert len(diffs) == 1
    assert diffs[0].path == "a.py"
    assert diffs[0].changed_lines == {2}


def test_diff_staged_real_repo(git_repo: Path) -> None:
    write_tree(git_repo, {"a.py": "x = 1\n"})
    commit_all(git_repo)
    write_tree(git_repo, {"a.py": "x = 2\n"})
    git(git_repo, "add", "-A")
    diffs = diff_staged(git_repo)
    assert len(diffs) == 1
    assert diffs[0].changed_lines == {1}


ABSENT_SHA = "0" * 40


def baseline_at(head: str | None) -> Baseline:
    return Baseline(metrics={"affected": 1}, confidence="High", spec=Spec(ref="base"), head=head)


class TestProvenance:
    def test_the_previous_commit_is_an_ancestor(self, git_repo: Path) -> None:
        write_tree(git_repo, {"a.py": "x = 1\n"})
        commit_all(git_repo)
        first = head_sha(git_repo)
        assert first is not None
        write_tree(git_repo, {"a.py": "x = 2\n"})
        commit_all(git_repo)
        provenance = check_provenance(git_repo, baseline_at(first))
        assert provenance.status == VERIFIED
        assert provenance.ok
        assert (provenance.recorded, provenance.current) == (first, head_sha(git_repo))

    def test_head_itself_is_an_ancestor_of_head(self, git_repo: Path) -> None:
        write_tree(git_repo, {"a.py": "x = 1\n"})
        commit_all(git_repo)
        assert check_provenance(git_repo, baseline_at(head_sha(git_repo))).ok

    def test_a_commit_on_a_divergent_branch_is_not_an_ancestor(self, git_repo: Path) -> None:
        # The shape of a baseline saved on one branch and used on another.
        write_tree(git_repo, {"a.py": "x = 1\n"})
        commit_all(git_repo)
        git(git_repo, "branch", "other")
        git(git_repo, "checkout", "-q", "other")
        write_tree(git_repo, {"a.py": "x = 2\n"})
        commit_all(git_repo)
        elsewhere = head_sha(git_repo)
        git(git_repo, "checkout", "-q", "-")
        write_tree(git_repo, {"a.py": "x = 3\n"})
        commit_all(git_repo)
        provenance = check_provenance(git_repo, baseline_at(elsewhere))
        assert provenance.status == NOT_ANCESTOR
        assert not provenance.ok

    def test_a_commit_this_repository_never_had_is_its_own_status(self, git_repo: Path) -> None:
        # A shallow clone, a force-push, or a rebase leaves this behind, and
        # it is a different finding from "on another branch".
        write_tree(git_repo, {"a.py": "x = 1\n"})
        commit_all(git_repo)
        assert check_provenance(git_repo, baseline_at(ABSENT_SHA)).status == UNKNOWN_COMMIT

    def test_a_baseline_with_no_recorded_commit(self, git_repo: Path) -> None:
        write_tree(git_repo, {"a.py": "x = 1\n"})
        commit_all(git_repo)
        assert check_provenance(git_repo, baseline_at(None)).status == NOT_RECORDED

    def test_a_repository_with_no_commits_says_so(self, git_repo: Path) -> None:
        assert check_provenance(git_repo, baseline_at("a" * 40)).status == NO_HEAD

    def test_commit_exists_answers_yes_and_no(self, git_repo: Path) -> None:
        write_tree(git_repo, {"a.py": "x = 1\n"})
        commit_all(git_repo)
        sha = head_sha(git_repo)
        assert sha is not None
        assert commit_exists(git_repo, sha)
        assert not commit_exists(git_repo, ABSENT_SHA)
        assert not commit_exists(git_repo, "not-a-ref-at-all")

    def test_is_ancestor_is_directional(self, git_repo: Path) -> None:
        write_tree(git_repo, {"a.py": "x = 1\n"})
        commit_all(git_repo)
        first = head_sha(git_repo)
        write_tree(git_repo, {"a.py": "x = 2\n"})
        commit_all(git_repo)
        second = head_sha(git_repo)
        assert first is not None and second is not None
        assert is_ancestor(git_repo, first, second)
        assert not is_ancestor(git_repo, second, first)
