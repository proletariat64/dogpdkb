# Configuration and self-contained runtime

Status: ready-for-agent
Type: task

Implement the TOML and runtime contracts in [the specification](../spec.md).
Validate defaults, overrides, secret-file configuration, proxy and CA handling,
and local doctor output. Use the existing provider code and keep runtime
defaults independent of environment variables.
