# agentkit design

This is the contract of the kit. It says what each brick does and never does, what the kit hands to Hypotrail, who may read what, how decisions are marked, what bounds the spending, where the kit stops, and how a person or the conductor drives it.

The kit is the producer side of an evaluation: it runs an assistant and exports what it answered. Hypotrail is the evaluator side: it holds the References, scores, compares and records the decision. Nothing flows back from Hypotrail into the kit's agents except what a person chooses to tell them.

The public twin is Hypotrail's Chinook prose Workload (`examples/chinook_prose/`): 30 `dev` Cases and 6 `public_check` Cases about the Chinook music-store database, answered in prose, with References and two authored fixture answer files (`answers-concise.jsonl`, `answers-verbose.jsonl`). The Chinook database is under the MIT licence; the Cases, References and fixtures are under CC0 1.0. The kit reads them from a Hypotrail checkout and copies none of them.

## 1. The bricks

| Brick | Does | Never does |
|---|---|---|
| **Cases** (`cases.py`) | Reads the Cases of one Partition of a Workload: `case_id`, `question`, `group`, `family` | Reads a References file; returns Cases of the held-out Partition outside the evaluator job |
| **Adapters** (`adapters/`) | Turn a Case into an answer. `scripted` replays the answer an authored fixture holds for the Case, with no model. `live` calls a model, opt-in and bounded | Receive a Reference or a score; call a model without the live flag, a key and a ceiling; run in CI |
| **Harness** (`harness.py`) | Runs an adapter over (Case, Repeat) pairs of one set. Appends a ledger record before the first call and closes it after the last; counts runs per set | Scores; writes a decision; runs the held-out set outside the evaluator job; spends beyond the budget |
| **Exporter** (`export.py`) | Writes `answers.jsonl` (one line per (Case, Repeat)) and `population.json` for `import agent`, from a closed harness run | Edits an answer; invents a value (an unknown measure is `null`); drops a failed or missing pair |
| **Decision log** (`decisions.py`) | Appends decisions about the kit's own protocol (which Configuration to keep trying, whether to resume after a stop), each marked `HUMAN` or `PROXY` | Lets a `PROXY` decision stand without its rule; lets the conductor write `HUMAN`; writes into a Hypotrail Study |
| **Conductor** (`conductor.py`) | In automatic mode, loops over Configurations on the dev set under the budget, records rule-based choices as `PROXY`, parks on a stop | Runs the held-out set; resumes after a stop; raises its own budget; writes `HUMAN` |
| **Evaluator job** (`evaluator.py`, `.github/workflows/evaluator.yml`) | The only place that runs the held-out set: it runs the harness on it, exports, and runs Hypotrail's `init`, `import agent`, `assess`, `compare` and `report` in the job; it publishes an aggregate report | Shows per-Case answers, questions or References to an agent session; runs from a local session; spends beyond its own ceiling |
| **Status** (`agentkit status`) | Read-only: runs per set, budget left, open stops, decisions and their marks | Writes anything |

## 2. The contract with Hypotrail

Hypotrail commit: the `import agent` that accepts `usage`, `metadata` and `settings` is on chadlis/hypotrail's branch `claude/s1-263` (head `941ffd8` when this was written). The kit pins that commit until it reaches `main`, then pins a `main` commit. A change of the pin is its own pull request, with the end-to-end test run against the new commit.

### 2.1 `answers.jsonl`

One JSON object per line, one line per (Case, Repeat) of the run, a `failed` or `missing` pair included. Keys, as `import agent` reads them:

| Key | When | Value |
|---|---|---|
| `case_id` | always | A Case ID of the Partition |
| `repeat` | always | An integer, from 0 |
| `observation_status` | always | `produced`, `failed` or `missing` |
| `raw_answer` | `produced` | The text the assistant returned, whole |
| `outcome` | optional | `answered`, `needs_clarification` or `not_answerable`, when the assistant declares it |
| `origin` | optional | Where the line comes from: `authored_fixture` for the scripted adapter, `external_run` for the live one |
| `producer` | optional | `{model, generation_time, historical_protocol, tool_use}`, strings |
| `usage` | optional | An object of models (or sub-steps), each `{tokens, latency_seconds, cost_usd}`, each a non-negative number or `null` |
| `metadata` | optional | A JSON object: the conversation id and turn, tags, the route an answer took |
| `settings` | optional | A flat object of strings, numbers, booleans or `null`: Configuration, model, temperature, seed, data snapshot |

`final_answer` (an already-parsed object) is accepted by Hypotrail; the kit does not write it, because the twin is answered in prose.

Hypotrail records `settings` as declared only when every line holds the same map; otherwise it records them unknown. The exporter therefore writes the run's settings once per run, identical on every line, and keeps per-line facts in `metadata`.

### 2.2 `population.json`

`{"episodes": [{"case_id": "D01", "repeat": 0}, ...]}`: every pair of the run, all Cases of one Partition. Hypotrail requires exactly one line of `answers.jsonl` per pair listed.

### 2.3 The Hypotrail commands

```
hypotrail init --workload chinook_prose --study STUDY
hypotrail import agent --study STUDY --source answers.jsonl --population population.json --request-id ID
hypotrail assess --study STUDY --observations OBSERVATION_SET_ID --scorer text_facts_v1 --references REFERENCES_ID
hypotrail compare --study STUDY --control ASSESSMENT_ID --candidate ASSESSMENT_ID
hypotrail report --study STUDY --out report.html
```

What the Study records of the kit is what the file declares (`declared`) or cannot establish (`unknown`); nothing is `verified`. The kit never claims, in a line, that it worked independently.

## 3. The wall

| Who | dev Cases | dev References | held-out Cases | held-out References | Per-Case results, held-out | Aggregate report, held-out |
|---|---|---|---|---|---|---|
| Kit agents and the conductor | read | never | never | never | never | after the decision only, if a person shares it |
| Agent guide (the person's chat session) | read | never | never | never | never | read |
| The person | read | read in Hypotrail | read | read in Hypotrail | after the decision | read |
| Evaluator job | read | through Hypotrail only | read | through Hypotrail only | writes, inside the job | writes |

The held-out set is the `public_check` Partition of the twin.

**What holds, and what does not.**
- `agentkit.cases` and the harness refuse the held-out Partition unless they are called under the evaluator entry point (`agentkit.evaluator`), which checks that it runs in a GitHub Actions job of `.github/workflows/evaluator.yml` (`GITHUB_ACTIONS` and `GITHUB_WORKFLOW_REF`). A refused run is checked before its adapter is built: it exits 9, marks a stale `running` record `failed` as any run does, writes a ledger record `refused` with no pair, and calls no adapter; exporting it is refused (exit 2). This is a refusal in code, testable. It is a guard, not a wall: the entry point reads the environment of a CI job, which a local process can imitate.
- On the twin, the held-out Cases and References are public files of a Hypotrail checkout. The kit demonstrates the pattern; it protects nothing that is not already public.
- A real wall comes from the place: the held-out set stored where only the evaluator job's identity can read it, and a job identity the agents do not have. The workflow runs in a protected GitHub environment with a required reviewer; the storage and the identities are the deploying team's to provide.

## 4. The decision log

`decisions/log.jsonl`, one JSON object per decision, in the form `kit-decision/1` (`contracts/kit-decision-1/`; it adds `schema_version` to the fields below):

| Field | Value |
|---|---|
| `id`, `time` | A sequence id and a UTC timestamp |
| `question`, `option` | What was decided, and the option kept |
| `mark` | `HUMAN` or `PROXY` |
| `rule` | `PROXY` only, required: the rule applied, by name and version |
| `words` | `HUMAN` only, required: the person's own words, whole, read from a file the person wrote |
| `source` | What the decision cites: an object of `run`, `stop` and `pull_request` (a pull request as `https://github.com/<owner>/<repo>/pull/<n>@<40-hex merge commit>`) |
| `by` | The process that wrote the line (`conductor`, `cli`, `evaluator`) |

- A `PROXY` line without a `rule`, or a `HUMAN` line without `words`, is refused.
- The conductor's process refuses to write `HUMAN`, whatever it is given.
- `HUMAN` means "a person's words were transcribed"; it does not authenticate anyone. The authenticated act is a pull request merged from a human account. The log says which.
- `agentkit cite ID` prints the line's citation (`kit-citation/1`), which a Hypotrail Decision may hold as its `source`. Its level is derived, never stored: `pr` for a `HUMAN` line that cites a pull request in that form, `relayed` for any other `HUMAN` line, `none` for a `PROXY` line.
- `keep-higher-dev-score@1` (`agentkit.rules`) answers `keep_after_comparison` from the two dev `text_facts_success` shares of a Comparison: it keeps the Candidate when its share is higher, and the Control otherwise, a tie included (drive, 2026-11-26).
- These marks belong to the kit. They never become Decisions in a Hypotrail Study, and a Hypotrail Decision never becomes a kit mark: the two are reviewed apart.

## 5. The budget

`budget.json`, versioned with the code:

- **dev runs**: the number of harness runs allowed on the dev set in the current cycle.
- **held-out readings**: the number of evaluator runs allowed per frozen Configuration (one by default).
- **live ceiling**, per invocation: maximum model calls, tokens and spend.

Every limit is checked before spending: a run that would exceed it is refused and recorded, not started. A ceiling is raised only by a commit to `budget.json` with its reason, through a pull request.

The kit reads `budget.json` from its working directory, the one that holds `ledger/`; run from the repository root, that is the versioned file. A missing or malformed file is invalid input (exit 2).

| Key | Value |
|---|---|
| `reason` | Why the limits are what they are: the commit that raises one says so here |
| `dev_runs` | An integer: dev runs allowed in the cycle. The cycle is the whole ledger of the working directory; a dev run counts unless it was `refused` |
| `held_out_readings` | An integer: held-out runs allowed per Configuration (the run's `settings`); a held-out run counts unless it was `refused` |
| `live` | `{calls, tokens, usd}`: the ceiling of one live invocation. A ceiling of 0 refuses every live run |
| `prices` | By model id, `{input_usd_per_mtok, output_usd_per_mtok, as_of, source}`: prices in USD per million tokens, each above 0 so that the spend ceiling binds, the date they were read and the public pricing page they were read from, as text |

A run that would exceed `dev_runs` or `held_out_readings`, or a live run under a ceiling of 0, exits 8 with a ledger record `refused` that names the limit in its `reason`, and calls no adapter.

**The live adapter** (drive, 2026-11-26; questions of chadlis/agentkit#5):
- Anthropic only, through the Messages API, called with the Python standard library. The model id is given explicitly (`--model`); there is no default model.
- It needs `--live`, the key in `AGENTKIT_ANTHROPIC_API_KEY`, and a price for its model in `budget.json`. Without any of them the run is refused before any call, with exit 2, and nothing is recorded.
- Before each call it checks the ceiling: the calls made, the tokens counted and the spend so far, and it bounds the call's output tokens to what remains of the token and spend ceilings. When a limit is reached, the harness closes the run `failed` with the limit as its `reason` (`call_ceiling`, `token_ceiling` or `cost_ceiling`); `agentkit run` exits 8.
- A call that fails (an HTTP status, an unreachable provider, a timeout, a reply that is not the API's) gives a `failed` line with `metadata.reason` `provider_error` and the error, and the harness closes the run `failed` with that reason; `agentkit run` exits 1. Its tokens and cost are unknown: `null`.
- Such a run, closed by the harness with a stated reason, is exported; a `failed` run without a reason (one that crashed) is not. Every pair the run did not send to the model is a `missing` line, whose `metadata` carries the reason: a pair never sent was not attempted. `failed` means the model was called and the call failed.

## 6. The stops

| Exit | Meaning |
|---|---|
| 0 | Done |
| 2 | Invalid input (a Case file, a fixture, a budget file) |
| 3 | A stop rule fired on this run: a stop file is written |
| 5 | An open stop blocks new runs |
| 7 | Gate: a blocking question has no `HUMAN` answer |
| 8 | Budget: this run would exceed a limit |
| 9 | Held-out refused outside the evaluator job |

A stop is resumed only by a `HUMAN` decision that cites it. In automatic mode, a stop parks the conductor and leaves a note for the person.

## 7. Driving the kit

**Accompanied.** The person works in a chat session (the agent guide) with the kit's repository open. The guide reads `agentkit status` before proposing a run, says what it costs in budget, and runs only what the person asks. After a run it points to the ledger record and the exported files; it shows numbers where they are written and does not retype them. The person imports the export into a dev Study of Hypotrail and decides there, in their own words. The guide says who made each choice: the person, a `PROXY` rule, or the conductor.

**Automatic.** The conductor runs alone on the dev set: it tries Configurations within the dev budget, records each rule-based choice as `PROXY`, and parks on any stop, gate or budget limit. It never touches the held-out set. When the person returns, the agent guide reads the ledger and the log and tells what happened: runs spent, `PROXY` decisions, parked stops, what waits for them. The guide does not iterate in this mode.

**The held-out pass**, in both modes, is the evaluator job, started by a person.
