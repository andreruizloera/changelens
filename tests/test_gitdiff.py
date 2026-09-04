from __future__ import annotations

from pathlib import Path

from changelens.gitdiff import diff_against_ref, diff_staged, parse_unified_diff
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
