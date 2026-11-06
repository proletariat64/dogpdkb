# Pipeline completion and recovery diagnostics

Type: task
Status: claimed
Blocked by: 01, 02

Spec: [spec.md](../spec.md)

Implement spec decisions 5 and 6 in pipeline: validate corpus before completed, retain plan on downstream failure, block graph/Web, produce bounded failure summary with staging and executable same-configuration resume command; list failures explain full refresh. Tests at pipeline runner seam. Own sync_knowledge_base.py, pipeline tests, and user docs.

Use TDD, approved seams in spec Testing Decisions. No live calls. Integration branch: feat/enhance-sync-break, initial base 5d06e4c.
