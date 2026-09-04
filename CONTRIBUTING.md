# Contributing to changelens

Thanks for taking a look. Bug reports, false-positive and false-negative
examples, and new language analyzers are all welcome.

## Setup

```sh
git clone https://github.com/andreruizloera/changelens
cd changelens
uv sync
```

## Before opening a PR

```sh
uv run ruff format src tests
uv run ruff check src tests
uv run pytest
./demo.sh   # the demo must still produce a sensible report
```

## Ground rules

- The analyzer must never import or execute the code it analyzes. Text
  and `ast` only.
- Prefer a missed edge plus an honest confidence downgrade over a
  guessed edge presented as fact. If a heuristic cannot explain why it
  fired, it does not ship.
- New behavior needs a test. Bug fixes need a regression test that
  fails without the fix.
- Full type annotations, clean error messages, nonzero exits for
  expected failures.

## Adding a language analyzer

Implement the `LanguageAnalyzer` protocol in `src/changelens/analyzer.py`
(see `python_analyzer.py` for the reference implementation), register it
in `analyzers()`, and add graph and impact tests mirroring
`tests/test_graph.py`. Open an issue first so we can agree on module
naming semantics for the language.

## Reporting analysis errors

The most useful bug report is a minimal repo layout (a handful of small
files) plus the report you got and the report you expected. The test
helpers in `tests/conftest.py` make it easy to turn that directly into a
regression test.
