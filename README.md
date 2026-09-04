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

This is the real output of `./demo.sh`, which commits a change to
`refund_payment` in the bundled example project and runs changelens on it:

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
- CI: `changelens origin/main --json` to gate or annotate builds

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
changelens HEAD~1 --repo ~/src/myproject   # run against another repo
```

The mermaid output pastes directly into GitHub comments, GitLab, and
mermaid.live:

```sh
changelens HEAD~1 --mermaid | pbcopy
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
  report.py            terminal, JSON, and mermaid renderers
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

Treat "Potentially affected" as a review checklist, not a verdict, and
read the confidence reasons before trusting a Low-confidence report.

## Roadmap

See [ROADMAP.md](ROADMAP.md). Highlights: TypeScript analyzer over the
existing interface, `--fail-on` thresholds for CI gating, coverage-map
ingestion so "relevant tests" comes from observed execution rather than
imports alone.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `uv run pytest` and
`uv run ruff check` before opening a PR.

## License

MIT, see [LICENSE](LICENSE).

GitHub topics: static-analysis, git, developer-tools, impact-analysis,
code-intelligence
