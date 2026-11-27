# kit-status/1

Owner: chadlis/agentkit. Copy: chadlis/mlkit, `contracts/kit-status-1/`, byte for byte, pinned in its `contracts.lock.json`. The decision is D-172 on chadlis/hypotrail#75 (C6).

The `kit-status/1` object goes in `details` of a successful `<kit> status --json` response. The C6 response envelope has the fields `ok`, `code`, `exit`, `message` and `details`. The guide agent reads the status before proposing a run and shows its numbers where they are written.

## Files

| Schema | Describes |
|---|---|
| `status.schema.json` | the `details` of a successful `status --json` response |

JSON Schema 2020-12.

## Rules a schema cannot say

- `status` writes nothing.
- A decision's `level` is derived as in `kit-decision/1`; a decision line carries no words. A gate answer identifies its source line's mark as `HUMAN`, `RELAYED` or `PROXY` when that line exists.
- An exit number means something only with its repository: the guide reads the `code` of `--json`, never the number alone.

## Examples

`examples/status.json`. `examples/refused.jsonl`: one refused case per line.

## Tests

`tests/test_contracts.py`. `status --json` and `docs/guide-surface.json` (`guide-surface/1`) come with the status ticket of each kit.
