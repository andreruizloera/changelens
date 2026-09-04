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
