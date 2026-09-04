"""Command-line entry point for changelens."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from changelens import __version__
from changelens.gitdiff import GitError, diff_against_ref, diff_staged, repo_root
from changelens.impact import analyze
from changelens.report import render_json, render_mermaid, render_terminal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="changelens",
        description="See the blast radius of a code change before you merge it.",
    )
    parser.add_argument(
        "ref",
        nargs="?",
        help="git ref to diff against HEAD (e.g. HEAD~1, main, a commit sha)",
    )
    parser.add_argument("--staged", action="store_true", help="analyze staged changes instead")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable JSON report")
    parser.add_argument("--mermaid", action="store_true", help="emit a mermaid impact flowchart")
    parser.add_argument(
        "--repo", type=Path, default=None, help="repository path (default: current directory)"
    )
    parser.add_argument("--version", action="version", version=f"changelens {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.ref and args.staged:
        print("error: pass a ref or --staged, not both", file=sys.stderr)
        return 2
    if not args.ref and not args.staged:
        print(
            "error: nothing to analyze; pass a ref (e.g. changelens HEAD~1) or --staged",
            file=sys.stderr,
        )
        return 2
    if args.json and args.mermaid:
        print("error: pass --json or --mermaid, not both", file=sys.stderr)
        return 2

    try:
        root = repo_root(args.repo or Path.cwd())
        diffs = diff_staged(root) if args.staged else diff_against_ref(root, args.ref)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not diffs:
        target = "staged changes" if args.staged else f"range {args.ref}..HEAD"
        print(f"No changes found in {target}.")
        return 0

    report = analyze(root, diffs, staged=args.staged)
    if args.json:
        print(render_json(report))
    elif args.mermaid:
        print(render_mermaid(report))
    else:
        print(render_terminal(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
