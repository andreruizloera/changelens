"""Command-line entry point for changelens."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from changelens import __version__
from changelens.baseline import (
    DEFAULT_FILENAME,
    BaselineError,
    BaselineMissing,
    Spec,
    comparable,
    load,
    save,
)
from changelens.gate import (
    NUMERIC_METRICS,
    GateError,
    baseline_from,
    check_against,
    evaluate,
    metrics_of,
    needs_baseline,
    parse_conditions,
)
from changelens.gitdiff import GitError, diff_against_ref, diff_staged, head_sha, repo_root
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
        "Compare against a saved baseline instead of a constant:",
        '"affected>baseline+10" (10 files wider than the baseline),',
        # argparse %-expands help text, so a literal percent sign is doubled.
        '"affected>baseline+25%%" (a quarter wider than the baseline),',
        '"confidence>baseline" (less trustworthy than the baseline).',
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
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="write this run's numbers to the baseline file, for a later run to compare against",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"baseline file to read and write (default: {DEFAULT_FILENAME} at the repo root)",
    )
    parser.add_argument("--version", action="version", version=f"changelens {__version__}")
    return parser


def _baseline_path(root: Path, given: Path | None) -> Path:
    return given.expanduser() if given is not None else root / DEFAULT_FILENAME


def _shown(root: Path, path: Path) -> str:
    """The baseline path as the user would type it, when it sits in the repo."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return str(path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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

    wants_baseline = needs_baseline(conditions)
    if args.baseline is not None and not (wants_baseline or args.save_baseline):
        print(
            "error: --baseline names the file a baseline is read from or written to, and "
            "nothing here does either; add --save-baseline, or a condition that compares "
            'against it such as --fail-on "affected>baseline+10"',
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        root = repo_root(args.repo or Path.cwd())
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    spec = Spec(ref=args.ref, staged=bool(args.staged))
    path = _baseline_path(root, args.baseline)
    baseline = None
    if wants_baseline:
        # A relative condition with no usable baseline is an error, never a
        # pass: waving a branch through because a file was missing is exactly
        # the failure mode a gate exists to prevent.
        try:
            baseline = load(path)
        except BaselineMissing:
            written_with = "--staged" if args.staged else str(args.ref)
            print(
                f"error: no baseline at {_shown(root, path)}, and --fail-on compares against "
                f"one. Write it from the state you want to compare against: "
                f"changelens {written_with} --save-baseline",
                file=sys.stderr,
            )
            return EXIT_USAGE
        except BaselineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if not comparable(baseline, spec):
            print(
                f"error: the baseline at {_shown(root, path)} was taken against "
                f"{baseline.spec.describe()} and this run analyzes {spec.describe()}; those "
                f"are two different diffs, so their numbers are not comparable. Re-save the "
                f"baseline against {spec.describe()}, or gate against "
                f"{baseline.spec.describe()}.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        try:
            # Also checked inside evaluate; done here so an unusable baseline
            # costs milliseconds rather than a repository-wide parse.
            check_against(conditions, baseline)
        except GateError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE

    try:
        diffs = diff_staged(root) if args.staged else diff_against_ref(root, args.ref)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    report = analyze(root, diffs, staged=args.staged) if diffs else Report()
    try:
        gate = evaluate(report, conditions, baseline) if conditions else None
    except GateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    notes: list[str] = []
    save_error: str | None = None
    if args.save_baseline:
        if not report.changed:
            # Saving all zeros would poison the next comparison: a
            # documentation-only merge would make the following branch look
            # like it invented the whole radius by itself.
            notes.append(
                f"Baseline not saved: this range has no Python changes, so every count "
                f"would be zero and the next run would read as growth. "
                f"{_shown(root, path)} is unchanged."
            )
        else:
            try:
                save(path, baseline_from(report, spec, head_sha(root), _now(), __version__))
                counts = metrics_of(report)
                notes.append(
                    f"Baseline saved to {_shown(root, path)} ({spec.describe()}: "
                    f"affected = {counts['affected']}, confidence {report.confidence})"
                )
            except BaselineError as exc:
                save_error = str(exc)

    machine = args.json or args.mermaid
    gate_printed = False
    if not diffs and not args.json:
        # --json keeps its contract even on an empty range: a CI script that
        # pipes into jq should not have to special-case a sentence.
        target = "staged changes" if args.staged else f"range {args.ref}..HEAD"
        print(f"No changes found in {target}.")
        if gate is not None:
            print(render_gate(gate))
            gate_printed = True
    elif args.json:
        print(render_json(report, gate))
    elif args.mermaid:
        print(render_mermaid(report))
    else:
        print(render_terminal(report, gate))
        gate_printed = gate is not None

    if gate is not None and machine and not gate_printed:
        # Machine-readable stdout stays machine-readable; the human-facing
        # verdict goes to stderr, where a CI log still shows it.
        print(render_gate(gate), file=sys.stderr)
    for note in notes:
        print(note, file=sys.stderr if machine else sys.stdout)

    if save_error is not None:
        print(f"error: {save_error}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_GATE_FAILED if gate is not None and gate.failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
