# Draft, accept, and bind Markdown image inputs

Status: resolved
Type: task

Implement the draft and work-plan contracts in [the specification](../spec.md).
Extract and validate image occurrences with the same Markdown parser, support
HTML image sources and inline Base64, and freeze authorized inputs and effective
policy before provider admission.

## Answer

Implemented in `10e7f84`, with source-preserving insertion fixes in `3a34b57`.
Public command tests cover single and directory drafts, Markdown and HTML
references, inline Base64 staging, strict selection, and per-document download
permission. Publication tests cover links, HTML blocks, and nested lists.
