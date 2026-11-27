# Contract changes

The contracts this repository owns, under `contracts/` (all but `agent-answers-1/`, which is chadlis/hypotrail's and copied here). One line per change: date, contract, version, what changed, the pull request. A copying repository moves its copy in its own contract-pin pull request (D-172 on chadlis/hypotrail#75, rule 5).

| Date | Contract | Version | Change | Pull request |
|---|---|---|---|---|
| 2026-11-26 | `kit-decision/1`, `kit-citation/1` | 1 | First written, from C4 of D-172 | chadlis/agentkit#11 |
| 2026-11-26 | `kit-aggregate/1` | 1 | First written, from C5 of D-172 | chadlis/agentkit#11 |
| 2026-11-26 | `kit-status/1` | 1 | First written, from C6 of D-172 | chadlis/agentkit#11 |
| 2026-11-27 | `kit-aggregate/1` | 1 | A dated note in the README: the refusal of a second reading and of a candidate made after the first is not yet enforced across job runs (chadlis/agentkit#25; the job's Decision is chadlis/agentkit#26; the kit log line for these choices is held for the owner); the schema is unchanged | chadlis/agentkit#24 |
| 2026-11-27 | `kit-decision/1` | 1 | Conformance tests enforce the existing timestamp format | chadlis/agentkit#12 (tracking issue) |
| 2026-11-27 | `kit-aggregate/1` | 1 | Document the candidate field and the evaluator's publication boundary | chadlis/agentkit#12 (tracking issue) |
| 2026-11-27 | `kit-status/1` | 1 | Document the status object inside the C6 JSON response envelope | chadlis/agentkit#12 (tracking issue) |
