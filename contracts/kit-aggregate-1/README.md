# kit-aggregate/1

Owner: chadlis/agentkit. Copy: chadlis/mlkit, `contracts/kit-aggregate-1/`, byte for byte, pinned in its `contracts.lock.json`. The decision is D-172 on chadlis/hypotrail#75 (C5, the report half). The Decision half, the `file_relayed` value of `words.channel` in `decision/2`, is Hypotrail's (ADR 0006).

`aggregate.json` is the one report the evaluator job publishes: counts per outcome, named aggregates, the Hypotrail ids, and the job's Decision when there is one. The per-unit files stay in the job.

## Files

| Schema | Describes |
|---|---|
| `aggregate.schema.json` | `aggregate.json` |

JSON Schema 2020-12. The schema has no free-text field for a unit: no question, answer, Reference, entity id or per-unit value validates. Names in `aggregates` are fixed identifiers.

## Rules a schema cannot say

- A leak search over every published file finds no held-out question, answer, Reference text or entity id.
- The job's Decision is written after the report, from a words file a person wrote before the reading. Its `source` names the outside act: a pull request cited as `https://github.com/<owner>/<repo>/pull/<n>@<40-hex merge commit>`, or `job:https://github.com/<owner>/<repo>/actions/runs/<run id>;actor=<login>`. `authenticated` stays false; the level is derived at read.
- A second reading of the same frozen candidate is refused (exit 8); a candidate made after the first reading is refused.
  - Note, 2026-11-27: enforced across job runs through `readings.jsonl`, a record of earlier readings committed through a pull request (chadlis/agentkit#25; `docs/design.md` of chadlis/agentkit, section 5). The job counts its readings with its ledger's, and once the record holds a reading it refuses a candidate whose fixture was committed after the first reading's job commit; each refusal exits 8 and publishes nothing. The job cannot write the record: it publishes the reading's line, and a reading counts in later jobs once a person has merged that line. The job's Decision is chadlis/agentkit#26. The kit log line for these choices is held for the owner.

## Examples

`examples/aggregate.json` (no Decision yet) and `examples/aggregate-decided.json` (with the job's `file_relayed` Decision). The ids are placeholders and the commits zeros. `examples/refused.jsonl`: one refused case per line.

## Tests

`tests/test_contracts.py`. The producer side is agentkit #9's.
