# Roadmap

Honest future work. None of this is implemented yet.

## Near term

- **TypeScript analyzer.** Second implementation of the
  `LanguageAnalyzer` protocol (ES module and CommonJS imports,
  tsconfig path aliases), which will also pressure-test the interface.
- **Ref range syntax.** Accept `A..B` and `A...B` directly instead of
  always comparing against HEAD.
- **Percentage baselines.** `--fail-on "affected>baseline+25%"`, for
  repositories where a fixed file count is the wrong unit at both ends of
  the size range. Baseline gating itself shipped; see the README.
- **Baseline provenance checks.** A baseline records the commit it was
  taken at and nothing verifies it. Check that the recorded commit is an
  ancestor of this run's HEAD, and say so when it is not, so an unrelated
  baseline stops reading as a real comparison.
- **`--tests-only` output.** Print just the relevant test paths,
  newline-separated, for piping straight into `pytest`.

## Medium term

- **Coverage-map ingestion.** Read a `coverage.py` data file so
  "relevant tests" comes from observed execution rather than imports
  alone, and label the two sources separately.
- **Symbol-level transitive propagation.** Today symbol precision exists
  at distance 1 (callers vs importers); propagate which symbol chains
  actually carry the change further out.
- **Re-export tracking.** Follow `from x import y` chains through
  `__init__.py` re-exports so facade packages do not blur attribution.
- **Watch mode.** `changelens --watch` re-runs on save and keeps a live
  report in the terminal.

## Longer term

- **Go, Rust, and Java analyzers** over the same interface.
- **GitHub Action** that posts the report and mermaid graph as a PR
  comment.
- **Historical calibration.** Compare past reports against which files
  actually changed in follow-up fix commits to measure and publish the
  estimator's real precision and recall.
