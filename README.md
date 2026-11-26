# agentkit

A reference kit for evaluating a free-text agent outside Hypotrail. It runs an assistant over the Cases of a Workload, counts every run, refuses the held-out set outside a separate evaluator job, and exports one line per (Case, Repeat) in the format of Hypotrail's `import agent`. Hypotrail then scores, compares and records the decision.

It runs on a public twin only: the Chinook prose Workload (`chinook_prose`) of chadlis/hypotrail. Its patterns are meant to be copied by teams that evaluate their own assistant.

## Status

Under construction. `agentkit export` runs the `scripted` adapter on the dev set and writes the files `import agent` reads; the harness, the ledger, the live adapter, the decision log and the evaluator job are planned in the slice issue and its tickets.

## Layout

- `src/agentkit/`: the package. `cli.py` is the `agentkit` command.
  - `cases.py`: reads a Workload's Cases by Partition, never its References.
  - `adapters/`: the assistant adapters, `scripted` (replays authored fixture answers, no model) and, later, `live` (opt-in, bounded).
  - (planned) `harness.py`: runs an adapter over (Case, Repeat) pairs and counts every run in the ledger.
  - `rules.py`: the named decision rules, `keep-higher-dev-score@1` first.
  - `export.py`: writes `answers.jsonl` and `population.json` for `import agent`.
  - (planned) `decisions.py`: the decision log, with its `HUMAN` and `PROXY` marks.
  - (planned) `conductor.py`: the automatic loop on the dev set, under budget.
  - (planned) `evaluator.py`: the entry point of the evaluator job.
- `tests/`: offline tests; no network, no model call. `tests/e2e/test_dev_end_to_end.py` runs the export through Hypotrail's `init`, `import agent`, `assess`, `compare` and a proxy `decide`.
- `.github/workflows/`: `ci.yml` (pytest), `e2e.yml` (the whole suite against Hypotrail checked out at `hypotrail.pin`, or at `hypotrail_ref` when dispatched) and, later, `evaluator.yml` (the evaluator job, manual, in a protected environment).
- `hypotrail.pin`: the chadlis/hypotrail commit the kit is tested against.
- `contracts/`: the file formats shared with Hypotrail and mlkit (JSON Schemas and examples), the copied ones pinned in `contracts.lock.json`; their changes in `docs/contracts/CHANGELOG.md`.
- `docs/design.md`: the bricks, the contract with Hypotrail, the wall, the decision log, the budget, the stops, and the two modes.

## How it feeds Hypotrail

```
agentkit export --workload-dir hypotrail/examples/chinook_prose --set dev --adapter scripted --fixture concise --out OUT
  -> OUT/answers.jsonl, OUT/population.json
hypotrail import agent --study STUDY --source answers.jsonl --population population.json --request-id ID
hypotrail assess / compare / report
```

The scripted run's `settings` are the map each line of its fixture declares; a fixture whose lines declare different maps is refused (exit 2).

The format is the one of `import agent` at the Hypotrail commit in `hypotrail.pin`, written down in `contracts/agent-answers-1/`.

## Install

Needs [uv](https://docs.astral.sh/uv/) and Python 3.13.

```
uv sync --locked
uv run --locked pytest
```

The tests that need the twin are skipped without a Hypotrail checkout. To run them, as `e2e.yml` does:

```
git clone https://github.com/chadlis/hypotrail && git -C hypotrail checkout "$(cat hypotrail.pin)"
uv pip install ./hypotrail
HYPOTRAIL_CHECKOUT=hypotrail AGENTKIT_REQUIRE_TWIN=1 uv run --locked --no-sync pytest
```

## License

Not chosen yet.
