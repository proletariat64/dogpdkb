# Download concurrency

## Notes

User-approved default 2, range 1–3, --concurrency on sync/apply. Independent runtime and documentation frontier; integrated review follows both.

## Decisions so far

Whole-batch stages and public CLI boundary remain. Main coordinator owns durable state. Greptile only for AI review.

[02 help/contracts](issues/02-help-contracts.md) resolved: installed full help and operator/integration contracts cover the approved runtime option; doc guard and diff checks pass.

## Fog

None in the product contract. Implementation must account for main-thread signal ownership and existing child lock inheritance.
