# agentkit

A reference kit for evaluating a free-text agent outside Hypotrail. It runs an assistant over the Cases of a Workload, counts every run, refuses the held-out set outside a separate evaluator job, and exports one line per (Case, Repeat) in the format of Hypotrail's `import agent`. Hypotrail then scores, compares and records the decision.

It runs on a public twin only: the Chinook prose Workload (`chinook_prose`) of chadlis/hypotrail. Its patterns are meant to be copied by teams that evaluate their own assistant.

## Status

Under construction. Nothing runs yet: this repository holds the design (`docs/design.md`) and an empty package skeleton. The work is planned in the slice issue and its tickets.

## Planned layout

- `src/agentkit/`: the package.
  - `cases.py`: reads a Workload's Cases by Partition, never its References.
  - `adapters/`: the assistant adapters, `scripted` (replays authored fixture answers, no model) and `live` (opt-in, bounded).
  - `harness.py`: runs an adapter over (Case, Repeat) pairs and counts every run in the ledger.
  - `export.py`: writes `answers.jsonl` and `population.json` for `import agent`.
  - `decisions.py`: the decision log, with its `HUMAN` and `PROXY` marks.
  - `conductor.py`: the automatic loop on the dev set, under budget.
  - `evaluator.py`: the entry point of the evaluator job.
- `tests/`: offline tests; no network, no model call.
- `.github/workflows/`: `ci.yml` (pytest) and, later, `evaluator.yml` (the evaluator job, manual, in a protected environment).
- `docs/design.md`: the bricks, the contract with Hypotrail, the wall, the decision log, the budget, the stops, and the two modes.

## How it feeds Hypotrail

```
agentkit export --set dev ... -> answers.jsonl, population.json
hypotrail import agent --study STUDY --source answers.jsonl --population population.json --request-id ID
hypotrail assess / compare / report
```

The format is the one of `import agent` at the Hypotrail commit pinned in `docs/design.md`.

## Install

Needs [uv](https://docs.astral.sh/uv/) and Python 3.13.

```
uv sync --locked
uv run --locked pytest
```

## License

Not chosen yet.
