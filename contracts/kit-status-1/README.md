# kit-status/1

Owner: chadlis/agentkit. Copy: chadlis/mlkit, `contracts/kit-status-1/`, byte for byte, pinned in its `contracts.lock.json`. The decision is D-172 on chadlis/hypotrail#75 (C6).

The JSON `<kit> status --json` prints. The guide agent reads it before proposing a run and shows its numbers where they are written.

## Files

| Schema | Describes |
|---|---|
| `status.schema.json` | the object `status --json` prints |

JSON Schema 2020-12.

## Rules a schema cannot say

- `status` writes nothing.
- A decision's `level` is derived as in `kit-decision/1`; a decision line carries no words.
- An exit number means something only with its repository: the guide reads the `code` of `--json`, never the number alone.

## Examples

`examples/status.json`. `examples/refused.jsonl`: one refused case per line.

## Tests

`tests/test_contracts.py`. `status --json` and `docs/guide-surface.json` (`guide-surface/1`) come with the status ticket of each kit.
