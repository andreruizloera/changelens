#!/usr/bin/env bash
# Demo: copy the example project into a temp git repo, commit a change to
# payments/refund.py, and let changelens report the blast radius.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp -R "$HERE/examples/shopsys/." "$WORK/"
cd "$WORK"

git init -q
git -c user.name=demo -c user.email=demo@example.com add .
git -c user.name=demo -c user.email=demo@example.com commit -qm "baseline"

git apply "$HERE/examples/demo-change.patch"
git -c user.name=demo -c user.email=demo@example.com add .
git -c user.name=demo -c user.email=demo@example.com commit -qm "validate charge_id before refunding"

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
