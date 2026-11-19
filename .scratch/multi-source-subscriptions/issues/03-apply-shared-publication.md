# Apply mixed inputs and publish once

Status: resolved
Blocked by: 02

Route remote IDs only into Skylark requests; feed frozen directory inputs to the existing comparison and publication pipeline. Report collisions against the shared target including unselected sources. Validate frozen resume, stale plans, and content-based updates, reverts and no-op scans. Keep plans, status and graph artifacts coherent.

## Answer

Both source types use the same plan/apply/graph pipeline. Integration tests cover
mixed publication, directory-only refresh, no-op and absence, collisions, live
source edits after planning and modified frozen witnesses.
