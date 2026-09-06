#!/usr/bin/env bash
# Demo: copy the example project into a temp git repo, commit a change to
# payments/refund.py, and let changelens report the blast radius. Then save
# that report as a baseline, widen the branch, and gate on the growth.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp -R "$HERE/examples/shopsys/." "$WORK/"
cd "$WORK"

GIT=(git -c user.name=demo -c user.email=demo@example.com)

git init -q
"${GIT[@]}" add .
"${GIT[@]}" commit -qm "baseline"
# A stable name for the state the branch grew out of, so the baseline and the
# run that compares against it analyze the same range.
"${GIT[@]}" branch base

git apply "$HERE/examples/demo-change.patch"
"${GIT[@]}" add .
"${GIT[@]}" commit -qm "validate charge_id before refunding"

if command -v changelens >/dev/null 2>&1; then
    RUN=(changelens)
else
    RUN=(uv run --project "$HERE" changelens)
fi

echo "\$ changelens HEAD~1"
echo
"${RUN[@]}" HEAD~1

echo
echo "\$ changelens HEAD~1 --fail-on \"affected>4\" --fail-on \"tests=0\""
echo
set +e
GATED="$("${RUN[@]}" HEAD~1 --fail-on "affected>4" --fail-on "tests=0")"
STATUS=$?
set -e
# The blast radius above is unchanged; show the gate block it now ends with.
echo "..."
echo "$GATED" | tail -n 3
echo
echo "\$ echo \$?"
echo "$STATUS"

# The gate is the point of this step: if it stops failing here, the exit code
# pasted into the README has drifted from the tool and CI should say so.
if [ "$STATUS" -ne 1 ]; then
    echo "demo: expected the gate to fail with exit 1, got $STATUS" >&2
    exit 1
fi

echo
echo "\$ changelens base --save-baseline"
echo
SAVED="$("${RUN[@]}" base --save-baseline)"
echo "..."
echo "$SAVED" | tail -n 1

# The branch grows: a later commit reaches into a helper the billing side
# shares, which is exactly the change an absolute threshold cannot see.
git apply "$HERE/examples/demo-change-2.patch"
"${GIT[@]}" add .
"${GIT[@]}" commit -qm "let format_cents take a currency symbol"

echo
echo "\$ changelens base --fail-on \"affected>baseline\""
echo
set +e
GROWN="$("${RUN[@]}" base --fail-on "affected>baseline")"
STATUS=$?
set -e
echo "..."
echo "$GROWN" | tail -n 6
echo
echo "\$ echo \$?"
echo "$STATUS"

if [ "$STATUS" -ne 1 ]; then
    echo "demo: expected the baseline gate to fail with exit 1, got $STATUS" >&2
    exit 1
fi

# Everything the README pastes for these two steps, checked against what the
# tool just printed. If a line here drifts, CI goes red instead of the docs
# quietly going stale.
EXPECTED=(
    "Baseline saved to .changelens-baseline.json (ref base: affected = 6, confidence High)"
    "FAIL  affected>baseline  (actual: affected = 9, baseline 6, +3)"
    "3 files entered the radius since the baseline:"
    "billing/statements.py"
    "reports/monthly.py"
    "tests/test_statements.py"
)
BOTH="$SAVED
$GROWN"
for line in "${EXPECTED[@]}"; do
    # A here-string, not a pipe: `set -o pipefail` would otherwise turn the
    # SIGPIPE from a matching `grep -q` into a failure.
    if ! grep -qF -- "$line" <<<"$BOTH"; then
        echo "demo: the README pastes a line the tool no longer prints: $line" >&2
        exit 1
    fi
done
