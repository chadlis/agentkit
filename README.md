# agentkit

A reference kit for evaluating a free-text agent outside Hypotrail. It runs an assistant over the Cases of a Workload, counts every run, refuses the held-out set outside a separate evaluator job, and exports one line per (Case, Repeat) in the format of Hypotrail's `import agent`. Hypotrail then scores, compares and records the decision.

It runs on a public twin only: the Chinook prose Workload (`chinook_prose`) of chadlis/hypotrail. Its patterns are meant to be copied by teams that evaluate their own assistant.

## Status

Under construction. `agentkit run` runs the `scripted` adapter on the dev set, counted in the run ledger before its first call; `agentkit export` writes the files `import agent` reads from a closed ledger run; `agentkit status` prints the runs per set and per status. The held-out set (`public_check`) is refused outside the evaluator entry point: exit 9 and a ledger record `refused`, a guard in code and not a wall (`docs/design.md`, section 3). Every run is checked against `budget.json` before it spends: a run that would exceed a limit exits 8 with a ledger record `refused`. The `live` adapter (Anthropic, opt-in) needs `--live`, `--model`, a key in `AGENTKIT_ANTHROPIC_API_KEY` and a price in `budget.json`, and stops at its ceiling; the versioned budget keeps it off (ceiling 0, no price). `agentkit decide` appends a decision to `decisions/log.jsonl`, `PROXY` with its rule or `HUMAN` with a file of the person's words, and `agentkit cite` prints the citation a Hypotrail Decision holds as its `source`. A stop rule of `stops.json` that fires on a dev run exits 3 and writes `stops/STOP-<run>.json`; every later run exits 5 until a `HUMAN` decision cites it. `agentkit status` also lists the open stops and the decisions with their marks. `agentkit conduct` runs scripted Configurations on the dev set under the budget and the stops, has Hypotrail compare each with the Control, writes one `PROXY` decision per Comparison, and parks on a stop or a limit with a note; while it runs, its marker file refuses `decide --mark human` (a guard, not a wall). `agentkit evaluate`, run by the evaluator workflow, reads one frozen Configuration on the held-out set, has Hypotrail import, assess and report it inside the job, and publishes only `aggregate.json`, a short page and the reading's line for `readings.jsonl`. That record of earlier jobs' readings is extended by a person through a pull request, since the job cannot push; a candidate already in it, or made after its first reading, exits 8.

## Layout

- `src/agentkit/`: the package. `cli.py` is the `agentkit` command.
  - `cases.py`: reads a Workload's Cases by Partition, never its References; the held-out Partition only under the evaluator entry point.
  - `adapters/`: the assistant adapters, `scripted` (replays authored fixture answers, no model) and `live` (calls an Anthropic model, opt-in, bounded by the live ceiling; tested with a fake client only).
  - `budget.py`: reads `budget.json` of the working directory and checks dev runs, held-out readings per Configuration and the live ceiling before a run spends.
  - `harness.py`: runs an adapter over (Case, Repeat) pairs, with a ledger record `running` before the first call, closed after the last.
  - `ledger.py`: the run ledger, `ledger/runs.jsonl` of the working directory, one record per run, and the answers each run recorded under `ledger/answers/`. A run that crashes stays `running`; the next run marks it `failed`.
  - `rules.py`: the named decision rules, `keep-higher-dev-score@1` first.
  - `export.py`: writes `answers.jsonl` and `population.json` for `import agent`, from a `completed` ledger run, or one the harness stopped at the live ceiling (its unreached pairs are `missing`, with the reason).
  - `decisions.py`: the decision log, with its `HUMAN` and `PROXY` marks, and its citation. A process marked `conductor` or `evaluator` (`AGENTKIT_PROCESS`, inherited by its children) cannot write `HUMAN`: a guard against accidental writes, not a wall.
  - `stops.py`: the rules of `stops.json` and the stop files under `stops/`, open until a `HUMAN` decision cites them.
  - `conductor.py`: the automatic loop on the dev set, under budget: it runs Hypotrail `import agent`, `assess` and `compare` as a subprocess, in a temporary Study it deletes, keeps only the Comparison's delta, writes `PROXY` decisions under `keep-higher-dev-score@1`, and leaves a note in `ledger/conductor/`.
  - `markers.py`: the conductor's marker file, `ledger/conductor/<conduct_id>.json`, on which `decide --mark human` refuses while its process lives.
  - `evaluator.py`: the entry point of the evaluator job; it checks that it runs in a job of `evaluator.yml`.
  - `evaluator_job.py`: the held-out reading under that entry point: the harness, the export and Hypotrail's Study in a temporary directory it deletes, the reading counted in the ledger without its pairs, and the aggregate report and record line it publishes.
  - `readings.py`: the readings record, `readings.jsonl` of the working directory, checked before a reading: the count across job runs, and the refusal of a candidate made after the first reading.
- `tests/`: offline tests; no network, no model call (`test_offline.py` reruns the suite with every socket blocked). `tests/e2e/test_dev_end_to_end.py` runs the export through Hypotrail's `init`, `import agent`, `assess`, `compare` and a proxy `decide` whose `--source` is the `agentkit cite` line of the kit's own `PROXY` decision, and imports a three-repeat export and one with `failed` and `missing` lines. `tests/e2e/test_conduct.py` runs `agentkit conduct` against the pinned Hypotrail, and `tests/e2e/test_evaluate.py` runs `agentkit evaluate` there with the variables of an evaluator job imitated.
- `.github/workflows/`: `ci.yml` (pytest), `e2e.yml` (the whole suite against Hypotrail checked out at `hypotrail.pin`, or at `hypotrail_ref` when dispatched) and `evaluator.yml` (the evaluator job: manual dispatch only, in the protected environment `held-out`, `contents: read` and no secret).
- `budget.json`: the kit's limits and model prices, raised only by a pull request that says why.
- `stops.json`: the stop rules and their thresholds.
- `readings.jsonl`: one line per held-out reading of an earlier evaluator job (candidate, Configuration digest, job commit, status), appended by a person through a pull request from the line the job publishes.
- `decisions/log.jsonl`: the kit's decision log (`kit-decision/1`), appended by `agentkit decide`. `ledger/` and `stops/` are not committed.
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
  -> dev: 1 run (1 completed, 0 failed, 0 refused, 0 running), the open stops and the decisions
agentkit decide --mark proxy --rule NAME@VERSION --question TEXT --option TEXT --run r-0001
agentkit decide --mark human --words-file FILE --question TEXT --option TEXT --stop STOP-r-0002
agentkit conduct --workload-dir hypotrail/examples/chinook_prose --set dev --configurations concise,verbose
  -> r-0001, r-0002, one PROXY decision, and ledger/conductor/c-0001-note.md
agentkit evaluate --workload-dir hypotrail/examples/chinook_prose --candidate concise --out published
  -> published/aggregate.json, published/aggregate.md and published/reading.jsonl, in a job of evaluator.yml only;
     a person appends reading.jsonl to readings.jsonl through a pull request
agentkit cite K-0001
  -> kit:chadlis/agentkit@<commit>:decisions/log.jsonl#K-0001;mark=PROXY;level=none, once the line is committed
hypotrail import agent --study STUDY --source answers.jsonl --population population.json --request-id ID
hypotrail assess / compare / report
```

`--repeats N` runs every Case N times (`repeat` 0 to N-1), one line per (Case, Repeat); a failed pair and a pair the run did not reach keep their lines, `failed` and `missing` (`docs/design.md`, section 2.4). `agentkit export` given the options of `run` instead of `--run` makes a new run and exports it. Exporting any other run (a crashed or refused one) is refused (exit 2).

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
