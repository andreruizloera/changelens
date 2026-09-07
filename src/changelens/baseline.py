"""A saved report, so a later run can gate on GROWTH instead of on size.

An absolute threshold is the wrong instrument in a large repository:
`--fail-on "affected>20"` trips on a routine pull request there, and a gate
that fires on every pull request gets turned off. The question worth gating
on is whether THIS branch widened the radius. A baseline is one run's
numbers written to a file, and a condition can compare against it:
`--fail-on "affected>baseline+10"`.

Two reports are only comparable when they answer the same question, so a
baseline records the SPEC it was taken with (the ref, or --staged) and a run
refuses to compare against a baseline taken with a different one. It also
records the commit it was taken at, and a run checks that commit against its
own history: see Provenance below and check_provenance in gitdiff.py.

This module is the data and its file format. It knows nothing about reports,
metrics, or git; gate.py builds a Baseline out of a Report and decides what a
metric name means, and gitdiff.py is the module that asks git the provenance
question and hands back the Provenance defined here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_FILENAME = ".changelens-baseline.json"

# metric name -> the files behind that metric when it was measured
FileSets = dict[str, tuple[str, ...]]


class BaselineError(Exception):
    """A baseline that could not be read, written, or compared."""


class BaselineMissing(BaselineError):
    """No baseline file where one was expected.

    Separate from BaselineError because it is the one failure with an
    obvious next step: write a baseline.
    """


@dataclass(frozen=True)
class Spec:
    """What a run analyzed. Two reports compare only when these match."""

    ref: str | None = None
    staged: bool = False

    def describe(self) -> str:
        return "staged changes" if self.staged else f"ref {self.ref}"


@dataclass(frozen=True)
class Baseline:
    metrics: dict[str, int]
    confidence: str
    spec: Spec = field(default_factory=Spec)
    files: FileSets = field(default_factory=dict)
    head: str | None = None  # the commit it was taken at; see Provenance
    created: str | None = None
    version: str | None = None


# Where the baseline's recorded commit sits relative to this run.
VERIFIED = "verified"  # an ancestor of this run's HEAD: a real comparison
NOT_ANCESTOR = "not_ancestor"  # a real commit, but on divergent history
UNKNOWN_COMMIT = "unknown_commit"  # not in this repository at all
NOT_RECORDED = "not_recorded"  # the baseline names no commit
NO_HEAD = "no_head"  # this run has no HEAD to compare against

_SHORT = 7


def _short(sha: str | None) -> str:
    return "an unrecorded commit" if sha is None else sha[:_SHORT]


@dataclass(frozen=True)
class Provenance:
    """Whether a baseline was taken somewhere this run descends from.

    A baseline from an unrelated branch, or from before a history rewrite,
    reads exactly like a real comparison and produces a verdict about
    nothing. Checking the recorded commit is what separates the two.

    "Not an ancestor" and "not in this repository" are kept apart on purpose.
    The first is a live commit on divergent history, which is what a branch
    that forked before the baseline was taken looks like, and is often a
    comparison the user meant to make. The second is a shallow clone, a
    force-push, or a rebase, and nothing at all can be said about it.
    """

    status: str
    recorded: str | None = None  # the commit the baseline was taken at
    current: str | None = None  # this run's HEAD

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED

    @property
    def label(self) -> str | None:
        """How the verdict should be qualified, or None when it stands alone."""
        if self.ok:
            return None
        if self.status == NOT_ANCESTOR:
            return "baseline is not from this history"
        return "baseline provenance unverified"

    def describe(self) -> str:
        """One sentence: what was found, and why it matters. No advice."""
        if self.status == NOT_ANCESTOR:
            return (
                f"the baseline was taken at commit {_short(self.recorded)}, which is not an "
                f"ancestor of this run's HEAD ({_short(self.current)}), so the two runs sit "
                f"on divergent history and these numbers may be comparing two different "
                f"branches rather than measuring growth"
            )
        if self.status == UNKNOWN_COMMIT:
            return (
                f"the baseline was taken at commit {_short(self.recorded)}, which is not in "
                f"this repository at all, which is what a shallow clone, a force-push, or a "
                f"rebase leaves behind, so nothing about where it came from can be checked"
            )
        if self.status == NO_HEAD:
            return (
                f"the baseline was taken at commit {_short(self.recorded)}, and this run has "
                f"no HEAD commit to compare it against, so its provenance cannot be checked"
            )
        return (
            "the baseline records no commit, so where it was taken cannot be checked; "
            "baselines written by changelens 0.1.0 and later record one"
        )


def to_dict(baseline: Baseline) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "changelens_version": baseline.version,
        "created": baseline.created,
        "spec": {"ref": baseline.spec.ref, "staged": baseline.spec.staged},
        "head": baseline.head,
        "confidence": baseline.confidence,
        "metrics": dict(baseline.metrics),
        "files": {name: list(files) for name, files in baseline.files.items()},
    }


def from_dict(payload: object, source: str) -> Baseline:
    """Read a baseline, or raise BaselineError naming the file and the problem."""
    if not isinstance(payload, dict):
        raise BaselineError(f"{source} is not a changelens baseline (expected a JSON object)")

    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise BaselineError(
            f"{source} is baseline schema_version {version!r}; this changelens reads "
            f"version {SCHEMA_VERSION}. Re-save it with --save-baseline."
        )

    metrics_raw = payload.get("metrics")
    if not isinstance(metrics_raw, dict) or not metrics_raw:
        raise BaselineError(f"{source} has no metrics object")
    metrics: dict[str, int] = {}
    for name, value in metrics_raw.items():
        # bool is an int in Python, and a JSON true here means a corrupt file.
        if not isinstance(value, int) or isinstance(value, bool):
            raise BaselineError(f"{source} has a non-integer value for metric {name!r}")
        metrics[str(name)] = value

    confidence = payload.get("confidence")
    if not isinstance(confidence, str):
        raise BaselineError(f"{source} has no confidence level")

    spec_raw = payload.get("spec")
    if not isinstance(spec_raw, dict):
        raise BaselineError(f"{source} does not record what it was taken against")
    ref = spec_raw.get("ref")
    if ref is not None and not isinstance(ref, str):
        raise BaselineError(f"{source} records a ref that is not a string")
    spec = Spec(ref=ref, staged=bool(spec_raw.get("staged")))

    files: FileSets = {}
    files_raw = payload.get("files")
    if files_raw is not None:
        if not isinstance(files_raw, dict):
            raise BaselineError(f"{source} has a files entry that is not an object")
        for name, entries in files_raw.items():
            if not isinstance(entries, list) or any(not isinstance(e, str) for e in entries):
                raise BaselineError(f"{source} has a files entry for {name!r} that is not paths")
            files[str(name)] = tuple(entries)

    return Baseline(
        metrics=metrics,
        confidence=confidence,
        spec=spec,
        files=files,
        head=payload.get("head") if isinstance(payload.get("head"), str) else None,
        created=payload.get("created") if isinstance(payload.get("created"), str) else None,
        version=(
            payload.get("changelens_version")
            if isinstance(payload.get("changelens_version"), str)
            else None
        ),
    )


def load(path: Path) -> Baseline:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BaselineMissing(f"no baseline file at {path}") from exc
    except OSError as exc:
        raise BaselineError(f"could not read the baseline at {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"{path} is not valid JSON: {exc}") from exc
    return from_dict(payload, str(path))


def save(path: Path, baseline: Baseline) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(to_dict(baseline), indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise BaselineError(f"could not write the baseline to {path}: {exc}") from exc


def comparable(baseline: Baseline, spec: Spec) -> bool:
    return baseline.spec == spec
