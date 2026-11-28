# kit-decision/1 and kit-citation/1

Owner: chadlis/agentkit. Copy: chadlis/mlkit, `contracts/kit-decision-1/`, byte for byte, pinned in its `contracts.lock.json`. Hypotrail holds no copy: it keeps a kit citation as declared text and checks nothing of it. The original decision is D-172 on chadlis/hypotrail#75 (C4). [D-215](https://github.com/chadlis/hypotrail/issues/75#issuecomment-7223332432) adds the relayed mark.

## Files

| Schema | Describes |
|---|---|
| `line.schema.json` | `kit-decision/1`: one line of `decisions/log.jsonl`, the kit's own decision log |
| `citation.schema.json` | `kit-citation/1`: the one line `<kit> cite K-NNNN` prints, which a Hypotrail Decision may hold as its `source` |

JSON Schema 2020-12. The log is at `decisions/log.jsonl` in both kits.

A citation reads `kit:<owner>/<repo>@<40-hex kit commit>:decisions/log.jsonl#<id>;mark=<HUMAN|RELAYED|PROXY>;level=<pr|human_unverified|relayed|none>`. The `cite` command prints it.

## Rules a schema cannot say

- A citation is not a translation. A Hypotrail Decision keeps the `author` its caller declares. No command turns a kit `HUMAN` into a Hypotrail `human`, or the reverse.
- The kit derives the level from the stored mark and source. A `HUMAN` line gets `pr` when `source.pull_request` has the ADR 0006 form. Other `HUMAN` lines get `human_unverified` in MLkit. The schemas also accept `relayed` for historical `HUMAN` citations and status responses and for Agentkit's current output. A `RELAYED` line gets `relayed`. A `PROXY` line gets `none`. [Owner decision, round 5](https://github.com/chadlis/hypotrail/issues/75#issuecomment-7235501369)
- The conductor's and the evaluator's processes cannot write `HUMAN`; the schema also refuses a `HUMAN` line whose `by` is not `cli`.
- A cited line exists at the cited kit commit.
- `HUMAN` means a person's words were transcribed. It authenticates no one.
- Evidence is ordered `HUMAN` > `RELAYED` > `PROXY`. A `RELAYED` line holds the owner's words verbatim, the HTTPS source that contains them, the relay's name in `relayed_by` and the fixed rule `owner-relayed@1`. It records a relay and authenticates no one. The conductor and evaluator cannot write it.

## Examples

`examples/log.jsonl`: a `PROXY` line and a `HUMAN` line. The `HUMAN` line's words are written for this contract and are no person's. `examples/citations.txt` cites both. `examples/refused.jsonl` and `examples/refused-citations.txt`: one refused case per line, each with the reason. The commits are zeros.

## Tests

`tests/test_contracts.py`. The producer side (the writer refuses `PROXY` without a rule, `HUMAN` without words, `HUMAN` from the conductor; `cite` and `status` derive the level) is agentkit #7's.
