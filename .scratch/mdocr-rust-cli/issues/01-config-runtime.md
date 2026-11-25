# Configuration and self-contained runtime

Status: resolved
Type: task

Implement the TOML and runtime contracts in [the specification](../spec.md).
Validate defaults, overrides, secret-file configuration, proxy and CA handling,
and local doctor output. Use the existing provider code and keep runtime
defaults independent of environment variables.

## Answer

Implemented in `54bc6e2`, with interrupted OAuth lock recovery completed in
`3a34b57`. The integrated tests cover strict TOML, local `doctor`, resolved
config paths, and frozen defaults. A loopback TLS proxy verified the explicit
proxy and custom CA through the public LLM command.
