# agentkit

A reference kit for evaluating a free-text agent outside Hypotrail. It runs an assistant over the Cases of a Workload, counts every run, refuses the held-out set outside a separate evaluator job, and exports one line per (Case, Repeat) in the format of Hypotrail's `import agent`. Hypotrail then scores, compares and records the decision.

It runs on a public twin only: the Chinook prose Workload (`chinook_prose`) of chadlis/hypotrail. Its patterns are meant to be copied by teams that evaluate their own assistant.

## Status

Under construction. `agentkit run` runs the `scripted` adapter on the dev set, counted in the run ledger before its first call; `agentkit export` writes the files `import agent` reads from a closed ledger run; `agentkit status` prints the runs per set and per status. The held-out set (`public_check`) is refused outside the evaluator entry point: exit 9 and a ledger record `refused`, a guard in code and not a wall (`docs/design.md`, section 3). The budget, the live adapter, the decision log, the conductor and the evaluator job are planned in the slice issue and its tickets.

## Layout

- `src/agentkit/`: the package. `cli.py` is the `agentkit` command.
  - `cases.py`: reads a Workload's Cases by Partition, never its References; the held-out Partition only under the evaluator entry point.
  - `adapters/`: the assistant adapters, `scripted` (replays authored fixture answers, no model) and, later, `live` (opt-in, bounded).
  - `harness.py`: runs an adapter over (Case, Repeat) pairs, with a ledger record `running` before the first call, closed after the last.
  - `ledger.py`: the run ledger, `ledger/runs.jsonl` of the working directory, one record per run, and the answers each run recorded under `ledger/answers/`. A run that crashes stays `running`; the next run marks it `failed`.
  - `rules.py`: the named decision rules, `keep-higher-dev-score@1` first.
  - `export.py`: writes `answers.jsonl` and `population.json` for `import agent`, from a `completed` ledger run only.
  - (planned) `decisions.py`: the decision log, with its `HUMAN` and `PROXY` marks.
  - (planned) `conductor.py`: the automatic loop on the dev set, under budget.
  - `evaluator.py`: the entry point of the evaluator job; it checks that it runs in a job of `evaluator.yml`. The job itself is planned.
- `tests/`: offline tests; no network, no model call. `tests/e2e/test_dev_end_to_end.py` runs the export through Hypotrail's `init`, `import agent`, `assess`, `compare` and a proxy `decide`.
- `.github/workflows/`: `ci.yml` (pytest), `e2e.yml` (the whole suite against Hypotrail checked out at `hypotrail.pin`, or at `hypotrail_ref` when dispatched) and, later, `evaluator.yml` (the evaluator job, manual, in a protected environment).
- `hypotrail.pin`: the chadlis/hypotrail commit the kit is tested against.
- `contracts/`: the file formats shared with Hypotrail and mlkit (JSON Schemas and examples), the copied ones pinned in `contracts.lock.json`; their changes in `docs/contracts/CHANGELOG.md`.
- `docs/design.md`: the bricks, the contract with Hypotrail, the wall, the decision log, the budget, the stops, and the two modes.

## How it feeds Hypotrail

```
agentkit run --workload-dir hypotrail/examples/chinook_prose --set dev --adapter scripted --fixture concise
  -> Run r-0001 completed (in ledger/runs.jsonl)
agentkit export --run r-0001 --out OUT
  -> OUT/answers.jsonl, OUT/population.json
agentkit status
  -> dev: 1 run (1 completed, 0 failed, 0 refused, 0 running)
hypotrail import agent --study STUDY --source answers.jsonl --population population.json --request-id ID
hypotrail assess / compare / report
```

`agentkit export` given the options of `run` instead of `--run` makes a new run and exports it. Exporting a run that is not `completed` is refused (exit 2).

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
