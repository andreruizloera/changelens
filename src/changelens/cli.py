"""Command-line entry point for changelens."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from changelens import __version__
from changelens.gate import NUMERIC_METRICS, GateError, evaluate, parse_conditions
from changelens.gitdiff import GitError, diff_against_ref, diff_staged, repo_root
from changelens.impact import Report, analyze
from changelens.report import render_gate, render_json, render_mermaid, render_terminal

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_USAGE = 2

_FAIL_ON_HELP = "\n".join(
    [
        "fail (exit 1) when a threshold is crossed; repeatable.",
        'Quote it, or the shell eats ">": --fail-on "affected>20".',
        "Metrics:",
        *(f"  {name}: {what}" for name, what in NUMERIC_METRICS.items()),
        "  confidence: compared by risk, so high < medium < low",
        'Operators: > >= < <= = != . Examples: "affected>20", "tests=0",',
        '"confidence>=medium" (trips on Medium or Low).',
    ]
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="changelens",
        description="See the blast radius of a code change before you merge it.",
        # Raw text, so the --fail-on metric list keeps its line breaks.
        formatter_class=argparse.RawTextHelpFormatter,
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
    parser.add_argument(
        "--fail-on", action="append", default=None, metavar="EXPR", help=_FAIL_ON_HELP
    )
    parser.add_argument("--version", action="version", version=f"changelens {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.ref and args.staged:
        print("error: pass a ref or --staged, not both", file=sys.stderr)
        return EXIT_USAGE
    if not args.ref and not args.staged:
        print(
            "error: nothing to analyze; pass a ref (e.g. changelens HEAD~1) or --staged",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.json and args.mermaid:
        print("error: pass --json or --mermaid, not both", file=sys.stderr)
        return EXIT_USAGE

    # Parsed before touching git, so a typo in a gate fails in milliseconds
    # rather than after a repository-wide parse.
    try:
        conditions = parse_conditions(args.fail_on or [])
    except GateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        root = repo_root(args.repo or Path.cwd())
        diffs = diff_staged(root) if args.staged else diff_against_ref(root, args.ref)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    report = analyze(root, diffs, staged=args.staged) if diffs else Report()
    gate = evaluate(report, conditions) if conditions else None

    if not diffs and not args.json:
        # --json keeps its contract even on an empty range: a CI script that
        # pipes into jq should not have to special-case a sentence.
        target = "staged changes" if args.staged else f"range {args.ref}..HEAD"
        print(f"No changes found in {target}.")
        if gate is not None:
            print(render_gate(gate))
        return EXIT_OK

    if args.json:
        print(render_json(report, gate))
    elif args.mermaid:
        print(render_mermaid(report))
    else:
        print(render_terminal(report, gate))
    if gate is not None and (args.json or args.mermaid):
        # Machine-readable stdout stays machine-readable; the human-facing
        # verdict goes to stderr, where a CI log still shows it.
        print(render_gate(gate), file=sys.stderr)
    return EXIT_GATE_FAILED if gate is not None and gate.failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
