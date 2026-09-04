# Security policy

## Threat model

changelens parses untrusted repository contents. By design it never
imports or executes analyzed code: all analysis is text parsing via the
standard library `ast` module, and the only subprocess it runs is `git`
with fixed arguments. A hostile repository should at worst produce a
wrong or degraded report, never code execution.

The bundled `demo.sh` does execute `git` in a temp directory it creates
itself; it does not execute anything from the analyzed project.

## Reporting a vulnerability

If you find a way to make changelens execute analyzed code, escape the
repository root, or otherwise break the model above, please email
andre.x.ruizloera@gmail.com rather than opening a public issue. You will
get a response within a week. Please include a minimal reproducing
repository layout.

## Supported versions

Only the latest release receives fixes.
