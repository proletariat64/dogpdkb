# Source configuration and durable identity

Status: resolved

Introduce strict typed source configuration, selector validation and source-scoped identities. Store and run bindings preserve corpus/graph target ownership and prevent cross-source or cross-target resume. Update repository-owned config fixtures and pin validation through the CLI. Do not migrate production state or config.

## Answer

Strict source configuration and versioned source-bound records are implemented.
CLI tests cover mixed config, legacy rejection, duplicate keys and existing
Yuque baseline registration without inventing export or Graphify success.
