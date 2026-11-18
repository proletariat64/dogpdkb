# Semantic score routing policy

Status: ready-for-agent

## Problem Statement

A Semantic LLM check can assign a score with an ambiguity note. The original experiment rejected some high scores solely because of ambiguity and treated 3 as review. The accepted policy instead routes on the score and lets the operator explicitly enable an ambiguity veto on skips. The deterministic authored-body report must not silently change meaning or start sending document text to a service.

## Solution

Add an offline `mdcompare semantic-route` stage after `mdcompare semantic` and an external scoring/validation step. Consume a validated score manifest tied to the exact diff evidence and publish a distinct routing report. Compile if the score is strictly greater than the configured threshold. Otherwise skip unless the skip ambiguity veto is enabled and the score is ambiguous.

## User Stories

1. As an operator, I want the default split at 3, so that scores 1–3 skip and 4–5 compile.
2. As an operator, I want to set the threshold, so that I can choose a different compile boundary without changing code.
3. As an operator, I want an explicit yes/no ambiguity veto for skips, so that ambiguity alone does not override the numeric score by default.
4. As an operator, I want the veto to turn an ambiguous skip into compile, never an ambiguous compile into an error.
5. As an operator, I want a missing, invalid or stale score not to enable a skip.
6. As an operator, I want numeric scores to be tied to the compared diff, so that a score from another revision cannot be applied silently.
7. As an operator, I want the resulting report to distinguish proof of equality, a score-based skip, and unavailable extraction.
8. As a maintainer, I want the existing deterministic contract and Graphify baseline advancement left alone.

## Implementation Decisions

- Public seam: invoke the CLI with deterministic report, diff index and scored manifest. A score row identifies the book and document, an exact SHA-256 digest of its diff sidecar, a 1–5 integer and an ambiguity boolean. Omit a score row to leave that document unscored. Binding failures fail closed.
- Flag names: `--compile-threshold` (integer 1–5, default 3, strict greater-than) and `--skip-ambiguity-intercept yes|no` (default no). For `yes`, an ambiguous score at or below the threshold routes to COMPILE.
- Score routing applies only to available, completed authored-body comparisons with deterministic CHANGED status. Keep deterministic routes for NEW, EQUIVALENT, UNCERTAIN and UNAVAILABLE. This command does not execute the LLM or Graphify or rewrite the original report.
- Preserve original assessment ledgers. The experiment recomputation under the accepted 3/no policy is 454 compile, 607 skip, 97 unscored among 1,158 prepared documents; this is an experimental snapshot, not an input shipped in releases.

## Testing Decisions

- Test through the public CLI, using deterministic report/evidence fixtures and scores below, equal to, and above the threshold, ambiguity under each switch, invalid input, digest drift, and absence of score. Smoke the installed wheel offline.
- Use actual hosted PR/Greptile review; only merge and release after observed 5/5 and green required CI.

## Out of Scope

Model invocation, Graphify execution, unattended production route adoption, changes to scorer rubric or alignment, reusing past scores without binding, and external corpus/data publication.
