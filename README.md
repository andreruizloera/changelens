# changelens

See the blast radius of a code change before you merge it.

changelens reads a git diff, figures out which Python functions, classes,
and modules actually changed, walks a static import graph of your
repository, and prints the files and tests most likely to feel the
change, ranked by how directly they depend on it.

## Quickstart

```sh
git clone https://github.com/andreruizloera/changelens
cd changelens
./demo.sh
```

Or install it and run it inside any git repo:

```sh
uv tool install git+https://github.com/andreruizloera/changelens
cd your-python-repo
changelens HEAD~1
```

## Example output

This is the first part of the real output of `./demo.sh`, which commits a
change to `refund_payment` in the bundled example project and runs
changelens on it. The rest of the demo runs under [CI
gating](#ci-gating), [Running only the tests that
matter](#running-only-the-tests-that-matter), and [Gating on
growth](#gating-on-growth):

```
$ changelens HEAD~1

Change blast radius
-------------------

Changed:
  payments/refund.py::refund_payment

Direct dependents:
  api/refunds.py  (calls refund_payment)
  jobs/retry_refunds.py  (calls refund_payment)

Potentially affected:
  billing/invoices.py  (via api/refunds.py)
  webhooks/stripe.py  (via jobs/retry_refunds.py)

Relevant tests:
  tests/test_refunds.py  (via api/refunds.py)
  tests/test_webhooks.py  (via webhooks/stripe.py)

Confidence: High (all changed symbols resolved; import graph is complete)
```

## Why?

Reviewing a diff tells you what changed. It does not tell you what else
now behaves differently. Before merging, the questions that matter are:
who calls this function, which modules sit downstream, and which tests
actually exercise this path? Answering them by hand means grepping and
guessing. changelens answers them from the code itself, in about a
second, with an honest confidence label attached.

Useful moments:

- pre-review: paste the report into the PR so reviewers see the radius
- pre-merge: check that the "Relevant tests" list actually ran in CI
- refactoring: `changelens --staged` before committing a risky edit
- inner loop: `--tests-only` to run the tests your branch touches instead
  of the whole suite; see [Running only the tests that
  matter](#running-only-the-tests-that-matter)
- CI: `changelens origin/main --fail-on "affected>20"` to stop a wide
  change from merging without a human look
- long-lived branch: `--save-baseline` when the work starts, then
  `--fail-on "affected>baseline+10"` to catch the commit that quietly
  widened it

## Installation

Requires Python 3.12+ and git. No runtime dependencies beyond the
standard library. Not on PyPI yet; install from git or a clone:

```sh
uv tool install git+https://github.com/andreruizloera/changelens
# or, from a clone:
uv tool install /path/to/changelens
```

## Usage

```sh
changelens HEAD~1          # impact of the last commit
changelens main            # impact of everything since main
changelens abc1234         # impact since a specific commit
changelens --staged        # impact of what is staged right now
changelens HEAD~1 --json     # machine-readable report
changelens HEAD~1 --mermaid  # impact graph as a mermaid flowchart
changelens HEAD~1 --tests-only   # just the test paths, for a test runner
changelens HEAD~1 --repo ~/src/myproject   # run against another repo
changelens main --fail-on "affected>20"    # exit 1 if the radius is wide
changelens main --save-baseline            # record this radius for later
changelens main --fail-on "affected>baseline+10"   # exit 1 if it widened
changelens main --fail-on "affected>baseline+25%"  # or widened by a quarter
changelens main --fail-on "affected>baseline" --require-baseline-ancestor
```

The mermaid output pastes directly into GitHub comments, GitLab, and
mermaid.live:

```sh
changelens HEAD~1 --mermaid | pbcopy
```

### Running only the tests that matter

`--tests-only` prints the "Relevant tests" list and nothing else, one path
per line, in the same ranked order the report uses. This is the real output
of the demo's example project, run against its `base` branch:

```
$ changelens base --tests-only
tests/test_refunds.py
tests/test_statements.py
tests/test_webhooks.py

$ echo $?
0
```

So the loop while you work is:

```sh
T=$(changelens main --tests-only) && [ -n "$T" ] && pytest $T
```

**Guard the pipe.** When nothing downstream of a change is a test file,
stdout is empty and the explanation goes to stderr, because a runner reading
that stream would try to collect the sentence as a path. An unguarded
`pytest $(changelens main --tests-only)` would then run your whole suite,
which is a slow surprise rather than a wrong answer, but a surprise either
way. `--fail-on` still works alongside it: the gate block goes to stderr and
the exit code is still the gate's.

## CI gating

`--fail-on` turns the report into a build decision. A condition is written
the way the failure reads, so `--fail-on "affected>20"` means "fail when
more than 20 files are affected". The flag is repeatable, and the gate
fails if any single condition is true.

This is the second part of `./demo.sh`, run against the same change as
above:

```
$ changelens HEAD~1 --fail-on "affected>4" --fail-on "tests=0"

...
Gate: failed
  FAIL  affected>4  (actual: affected = 6)
  ok    tests=0     (actual: tests = 2)

$ echo $?
1
```

Metrics:

| metric | counts |
| --- | --- |
| `changed` | changed Python files |
| `direct` | files that import a changed module directly |
| `transitive` | files reached at distance 2 or more |
| `tests` | test files reached from the change |
| `affected` | all downstream files (direct + transitive + tests) |
| `distance` | longest import hop from a change to a dependent |
| `confidence` | `high`, `medium`, or `low` |

Operators are `>`, `>=`, `<`, `<=`, `=` (or `==`), and `!=`. The right side
is a number, a confidence level, or a comparison against a saved baseline
(`baseline`, `baseline+10`, `baseline-3`, `baseline+25%`); see [Gating on
growth](#gating-on-growth). Quote the expression, or your shell will read
`>` as a redirect; changelens says so by name if you forget.

Two behaviors are judgement calls, so they are stated rather than left to
be discovered:

- **Confidence compares by risk**, not alphabetically: `high < medium <
  low`. `--fail-on "confidence>=medium"` trips on a Medium or a Low
  report; `--fail-on "confidence=low"` trips only on Low.
- **The gate is skipped, not passed, when a diff has no Python changes.**
  Every count would be zero, and a condition like `tests=0` would
  otherwise fire on a documentation-only pull request. The report says
  `Gate: skipped` and the exit code is 0.

When a numeric condition passes on a Medium or Low report, the gate says
so: a degraded report under-counts (star imports and dynamic imports hide
edges), so its all-clear is weaker evidence than the same all-clear at
High confidence.

```
Gate: passed
  ok    affected>10  (actual: affected = 1)
  Note: confidence is Medium, so the counts this gate read can be
        lower than reality; a passing number is weaker evidence here.
```

Exit codes: `0` clean, `1` a gate condition tripped, `2` a usage, git, or
baseline error. A bad expression is rejected before git runs. Nothing else
moves the exit code: a warning about where a baseline came from stays a
warning unless you ask for more, and [Where the baseline came
from](#where-the-baseline-came-from) says how.

With `--json` or `--mermaid` the gate block goes to stderr so stdout stays
machine-readable, and the JSON report carries `metrics` and a `gate`
object with every condition, its threshold, its actual value, and whether
it tripped.

```yaml
- name: Blast radius
  run: |
    changelens origin/${{ github.base_ref }} \
      --fail-on "affected>20" --fail-on "confidence=low"
```

## Gating on growth

An absolute threshold is the wrong instrument in a large repository: if a
routine pull request there affects 40 files, `affected>20` fires on every
one of them, and a gate that always fires gets deleted. The question worth
gating on is whether this branch made the radius wider.

`--save-baseline` writes the current run's numbers to
`.changelens-baseline.json`, and a condition can compare against it with
`baseline`, optionally offset by a file count or a percentage:
`affected>baseline+10` means "fail when this run is more than 10 files wider
than the baseline", and `affected>baseline+25%` means "fail when it is more
than a quarter wider".

This is the third part of `./demo.sh`. It saves the radius of the refund
change, then commits a second change that reaches a formatting helper the
billing side shares:

```
$ changelens base --save-baseline

...
Baseline saved to .changelens-baseline.json (ref base: affected = 6, confidence High)

$ changelens base --fail-on "affected>baseline"

...
Gate: failed
  FAIL  affected>baseline  (actual: affected = 9, baseline 6, +3)
        3 files entered the radius since the baseline:
          billing/statements.py
          reports/monthly.py
          tests/test_statements.py

$ echo $?
1
```

Naming the files that entered is the point of storing more than a count: a
reviewer's next question after "+3" is always "which three".

### Percentage thresholds

A file count is the wrong unit at both ends of the repository size range:
ten files is noise in a four-thousand-file repository and a rewrite in a
forty-file one. So the offset can be a percentage instead, and **the
percentage is of the baseline's own value for that metric, not of the
repository total**: against a baseline of 6 affected files,
`affected>baseline+25%` is a threshold of 7.5 files.

This is the fourth part of `./demo.sh`, run against the same grown branch as
above, which affects 9 files against a baseline of 6:

```
$ changelens base --fail-on "affected>baseline+25%"

...
Gate: failed
  FAIL  affected>baseline+25%  (actual: affected = 9, baseline 6, +3, threshold 7.5)
        3 files entered the radius since the baseline:
          billing/statements.py
          reports/monthly.py
          tests/test_statements.py

$ echo $?
1

$ changelens base --fail-on "affected>baseline+50%"

...
Gate: passed
  ok    affected>baseline+50%  (actual: affected = 9, baseline 6, +3, threshold 9)

$ echo $?
0
```

Those two runs are the boundary: 50% of 6 is exactly 9, and a run sitting
exactly on the threshold does not trip `>`. One file more would.

The threshold is never rounded. Both sides of the comparison are multiplied
by 100 and compared as integers, so `affected>baseline+25%` against a
baseline of 6 asks whether `900 > 750`, and there is no rounding rule to
remember or to disagree with. The count the percentage worked out to is
printed on the line, exactly, so a gate written in one unit still reports in
the metric's own.

Percentages take the same shapes the file-count offsets do:
`affected>baseline+25%` fails on growth past a quarter,
`affected<baseline-25%` fails on a shrink past a quarter, and every numeric
metric accepts one. Offsets are whole numbers; `baseline+2.5%` is a usage
error rather than a silent reinterpretation.

**A percentage of a zero baseline is zero.** If the baseline recorded no
affected files, no percentage is room to grow, so any growth trips and a
still-empty radius does not. Nothing divides by the baseline, so this is
arithmetic rather than an error.

### Judgement calls

Four more behaviors here are judgement calls, so they are stated rather
than left to be discovered:

- **A missing baseline is an error, not a pass.** `--fail-on
  "affected>baseline"` with no baseline file exits 2 and prints the command
  that would write one. A gate that waves a branch through because a file
  was missing is a rubber stamp. If a missing baseline should be tolerated
  in your pipeline, that is one visible line of shell, below.
- **A baseline for a different range is refused.** The file records what it
  was taken against (`main`, `HEAD~1`, `--staged`), and comparing the radius
  of one diff to the radius of another is not growth, it is two answers to
  two questions. Mismatches exit 2 and name both.
- **A range with no Python changes never overwrites a baseline.** Every
  count there would be zero, and saving that would make the next branch look
  like it invented the entire radius by itself. The existing file is left
  alone and the run says so.
- **Confidence compares against the baseline too**, as
  `--fail-on "confidence>baseline"`: fail when this run is less trustworthy
  than the one the baseline came from. It takes no offset, because a step on
  a three-level risk scale is not a quantity. When the two runs read at
  different confidence levels, the gate says so, since some of the movement
  in the numbers is then edges becoming visible rather than impact changing.

### Where the baseline came from

A baseline that is real, readable, and taken against the same ref can still
be a comparison against nothing: a cached CI artifact from a different
branch, or one written before the history was rewritten. Its numbers land in
the report looking exactly like a real measurement.

So changelens checks the commit the baseline recorded against this run's
history, with `git merge-base --is-ancestor`. This is the last part of
`./demo.sh`. A baseline is saved on a side branch, and the branch that gates
against it affects the same 9 files, so the gate reads perfectly flat:

```
$ changelens base --fail-on "affected>baseline"   # baseline from another branch

...
Gate: passed (baseline is not from this history)
  ok    affected>baseline  (actual: affected = 9, baseline 9, no change)
  Warning: the baseline was taken at commit b790fea, which is not an ancestor
           of this run's HEAD (11c55f7), so the two runs sit on divergent
           history and these numbers may be comparing two different branches
           rather than measuring growth. Re-save the baseline from this
           branch, or pass --require-baseline-ancestor to make this an error
           instead of a warning.

$ echo $?
0
```

(The commit shas are from that run of the demo, and differ on yours because
the demo builds its repository fresh each time. The lines that carry no sha
are checked against the tool's real output by `demo.sh` in CI, so this block
cannot go stale quietly.)

**The default is a warning and a qualified verdict, not a refusal.** A branch
that forked before the baseline was taken is a legitimate comparison, and it
is not an ancestor either, so refusing outright would fire on ordinary work
and the gate would get deleted, which is the same argument this whole section
makes about absolute thresholds. What must not happen is a bogus baseline
reading as a clean bill of health, and it cannot here: the verdict line
itself carries the reason, so `Gate: passed` cannot be quoted out of the
block without it.

**The exit code stays the gate's own.** 0 or 1 says whether the conditions
tripped, and provenance never changes it silently. To make it a build
failure, ask for that: `--require-baseline-ancestor` exits 2 with the same
sentence, before any analysis runs, and is the one line to add to a pipeline
that would rather stop than read a warning.

```
$ changelens base --fail-on "affected>baseline" --require-baseline-ancestor
error: the baseline was taken at commit b790fea, which is not an ancestor of this run's HEAD (11c55f7), so the two runs sit on divergent history and these numbers may be comparing two different branches rather than measuring growth, and --require-baseline-ancestor was passed. Re-save the baseline at .changelens-baseline.json from this branch, or drop the flag to gate on it with a warning instead.

$ echo $?
2
```

Findings are kept apart rather than collapsed into one message, because they
call for different fixes:

| finding | what it means |
| --- | --- |
| not an ancestor | a real commit on divergent history, usually another branch |
| not in this repository | a shallow clone, a force-push, or a rebase dropped it |
| no commit recorded | a hand-written baseline, or one from a repo with no commits |
| no HEAD to compare | this run has no commits, so there is no history to check against |

The first is the one you can act on directly; the other three all mean the
question could not be answered, which is different from answering it "no".

The baseline file also records the timestamp it was taken at and the
changelens version that wrote it. Those stay provenance for a human to read.
What is still not checked is that the baseline came from the *same
repository*: a baseline copied between two clones that share history passes
the ancestry check.

`--baseline PATH` puts the file somewhere else (both to write and to read).
In CI, cache it per branch and decide for yourself what a first run means:

```yaml
- name: Blast radius growth
  run: |
    # The first run on a branch has nothing to compare against; that is a
    # decision for the pipeline to make out loud, not for changelens to
    # make quietly.
    if [ -f .changelens-baseline.json ]; then
      changelens origin/${{ github.base_ref }} --fail-on "affected>baseline+10"
    fi
    changelens origin/${{ github.base_ref }} --save-baseline
```

## How it works

1. **Diff.** `git diff -U0 <ref> HEAD` (or `--cached` for `--staged`) is
   parsed into per-file changed line ranges on the new side of the diff.
2. **Symbols.** Each changed file's HEAD (or index) version is parsed
   with the standard library `ast` module, and every changed line is
   mapped to its innermost enclosing `def` or `class`. Lines outside any
   definition mark the module itself as changed.
3. **Import graph.** Every Python file in the repository is parsed (never
   imported or executed) to build a conservative static import graph:
   `import x.y`, `from x import y`, and package-relative imports are
   resolved against the modules that actually exist in the repo. A
   `from a import b` edge is classified as a submodule import or a
   symbol import by checking whether `a.b` exists as a module.
4. **Call approximation.** Within each file, call sites whose target can
   be traced through that file's own import bindings (for example
   `refund.refund_payment(...)` after `from payments import refund`) are
   resolved to (module, symbol) pairs. This is a deliberate
   approximation: it only sees direct, statically obvious calls.
5. **Ranking.** Reverse edges from the changed modules are walked
   breadth-first. Distance-1 importers are direct dependents; modules
   that provably call a changed symbol rank above modules that merely
   import the module. Distance-2+ modules are "potentially affected",
   ordered by distance. Test files (under `tests/`, or named
   `test_*.py` / `*_test.py`) at any distance are collected separately
   as relevant tests.
6. **Confidence.** High only when every changed file parsed, every
   changed symbol resolved, and nothing was seen that could hide an
   edge. Star imports of changed modules, any `importlib` /
   `__import__` usage, or unparseable bystander files degrade it to
   Medium; an unparseable changed file or an unresolvable relative
   import degrades it to Low. Every downgrade prints its reason.

## Architecture

```
src/changelens/
  cli.py               argument parsing and exit codes
  gitdiff.py           git plumbing and unified-diff parsing
  analyzer.py          the per-language analyzer interface (Protocol)
  python_analyzer.py   the Python implementation (stdlib ast only)
  graph.py             repository-wide import graph construction
  impact.py            dependent discovery, ranking, confidence heuristic
  gate.py              --fail-on expressions, metrics, pass/fail decision
  baseline.py          the saved-report file format, its comparability,
                       and what its recorded commit turned out to be
  report.py            terminal, JSON, mermaid, and test-path renderers
```

v0.1 analyzes Python repositories only, so the analysis can be genuinely
good rather than superficially broad. The seam for other languages is
already in place: `analyzer.py` defines a small `LanguageAnalyzer`
protocol (claim files by extension, derive module names, extract import
and call facts, map changed lines to symbols, recognize test files), and
the git, graph, impact, and report layers consume analyzers only through
that interface. A TypeScript, Go, Rust, or Java analyzer is a new file
plus a registry entry, not a rewrite.

## Limitations

changelens is a conservative impact estimator, not perfect static
analysis. It over- and under-approximates in known ways, and it tells
you when its own inputs were degraded, but it cannot see:

- dynamic imports (`importlib.import_module`, `__import__`) - detected
  and reported as a confidence downgrade, but the hidden edges are not
  recovered
- reflection (`getattr`, `globals()[name]()`) and string-based dispatch
  tables
- monkeypatching, dependency injection, and plugin registries
- calls through instances or callbacks (`obj.method()` where `obj` came
  from anywhere non-obvious); the call-graph layer only resolves calls
  traceable through a file's own import bindings
- imports rooted anywhere other than the repository root or a top-level
  `src/` directory
- non-Python files; changed ones are listed as ignored, never silently
  dropped
- whether a baseline came from this *repository*; the spec it was taken with
  and the ancestry of the commit it was taken at are both checked, but two
  clones sharing history are indistinguishable here

Treat "Potentially affected" as a review checklist, not a verdict, and
read the confidence reasons before trusting a Low-confidence report.

## Roadmap

See [ROADMAP.md](ROADMAP.md). Highlights: TypeScript analyzer over the
existing interface, coverage-map ingestion so "relevant tests" comes from
observed execution rather than imports alone, and `A..B` range syntax
instead of always comparing against HEAD.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `uv run pytest` and
`uv run ruff check` before opening a PR.

## License

MIT, see [LICENSE](LICENSE).

GitHub topics: static-analysis, git, developer-tools, impact-analysis,
code-intelligence
