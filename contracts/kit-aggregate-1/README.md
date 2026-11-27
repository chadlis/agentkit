# kit-aggregate/1

Owner: chadlis/agentkit. Copy: chadlis/mlkit, `contracts/kit-aggregate-1/`, byte for byte, pinned in its `contracts.lock.json`. The decision is D-172 on chadlis/hypotrail#75 (C5, the report half). The Decision half, the `file_relayed` value of `words.channel` in `decision/2`, is Hypotrail's (ADR 0006).

`aggregate.json` is the one report the evaluator job publishes: counts per outcome, named aggregates, the Hypotrail ids, and the job's Decision when there is one. The per-unit files stay in the job.

## Files

| Schema | Describes |
|---|---|
| `aggregate.schema.json` | `aggregate.json` |

JSON Schema 2020-12. The schema reserves fields for counts and aggregates and specifies an identifier pattern for their names. It accepts `kit.candidate` as a string. The evaluator takes a scripted fixture name for this field and keeps the per-unit files inside the job. The publication test checks the report and its summary for held-out content.

## Rules a schema cannot say

- A leak search over every published file finds no held-out question, answer, Reference text or entity id.
- The job's Decision is written after the report, from a words file a person wrote before the reading. Its `source` names the outside act: a pull request cited as `https://github.com/<owner>/<repo>/pull/<n>@<40-hex merge commit>`, or `job:https://github.com/<owner>/<repo>/actions/runs/<run id>;actor=<login>`. `authenticated` stays false; the level is derived at read.
- A second reading of the same frozen Candidate or a reading beyond the committed total budget exits 8. The initial total is three ([D-220](https://github.com/chadlis/hypotrail/issues/75#issuecomment-7223332432)).
  - Note, 2026-11-27: not yet enforced across job runs. The second reading is refused within one working directory only, since a job's runner starts with an empty ledger, and the refusal of a candidate made after the first reading is not built. Both are tracked in chadlis/agentkit#25, which must land before the first real dispatch of `evaluator.yml`; the job's Decision is chadlis/agentkit#26. The kit log line for these choices is held for the owner.
  - Update, 2026-11-27: [D-234](https://github.com/chadlis/hypotrail/issues/75#issuecomment-7225276774) requires a durable reservation on `main` before protected access. Failed jobs keep their reservations. An owner-merged return restores a total slot while preserving the Candidate limit. See the kit design for the procedure. The aggregate schema and the outcome metadata fields are unchanged.

## Examples

`examples/aggregate.json` (no Decision yet) and `examples/aggregate-decided.json` (with the job's `file_relayed` Decision). The ids are placeholders and the commits zeros. `examples/refused.jsonl`: one refused case per line.

## Tests

`tests/test_contracts.py` checks the schema examples. `tests/e2e/test_evaluate.py` checks the evaluator's publication boundary.
