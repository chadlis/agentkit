# agentkit design

This is the contract of the kit. It says what each brick does and never does, what the kit hands to Hypotrail, who may read what, how decisions are marked, what bounds the spending, where the kit stops, and how a person or the conductor drives it.

The kit is the producer side of an evaluation: it runs an assistant and exports what it answered. Hypotrail is the evaluator side: it holds the References, scores, compares and records the decision. From Hypotrail back into the kit, the conductor reads the aggregates of a dev Comparison (the two shares of `comparison.delta`), and nothing else does: no per-Case result, no Reference, no Study file, nothing of the held-out set. Anything more reaches the kit's agents only when a person chooses to tell them (K-0003, a `PROXY` decision under D-166).

The public twin is Hypotrail's Chinook prose Workload (`examples/chinook_prose/`): 30 `dev` Cases and 6 `public_check` Cases about the Chinook music-store database, answered in prose, with References and two authored fixture answer files (`answers-concise.jsonl`, `answers-verbose.jsonl`). The Chinook database is under the MIT licence; the Cases, References and fixtures are under CC0 1.0. The kit reads them from a Hypotrail checkout and copies none of them.

## 1. The bricks

| Brick | Does | Never does |
|---|---|---|
| **Cases** (`cases.py`) | Reads the Cases of one Partition of a Workload: `case_id`, `question`, `group`, `family` | Reads a References file; returns Cases of the held-out Partition outside the evaluator job |
| **Adapters** (`adapters/`) | Turn a Case into an answer. `scripted` replays the answer an authored fixture holds for the Case, with no model. `live` calls a model, opt-in and bounded | Receive a Reference or a score; call a model without the live flag, a key and a ceiling; run in CI |
| **Harness** (`harness.py`) | Runs an adapter over (Case, Repeat) pairs of one set. Appends a ledger record before the first call and closes it after the last; counts runs per set | Scores; writes a decision; runs the held-out set outside the evaluator job; spends beyond the budget |
| **Exporter** (`export.py`) | Writes `answers.jsonl` (one line per (Case, Repeat)) and `population.json` for `import agent`, from a closed harness run | Edits an answer; invents a value (an unknown measure is `null`); drops a failed or missing pair |
| **Decision log** (`decisions.py`) | Appends decisions about the kit's own protocol (which Configuration to keep trying, whether to resume after a stop), each marked `HUMAN`, `RELAYED` or `PROXY` | Lets a `PROXY` decision stand without its rule; lets the conductor write `HUMAN` or `RELAYED`; writes into a Hypotrail Study |
| **Conductor** (`conductor.py`) | In automatic mode, runs scripted Configurations on the dev set under the budget and the stops; has Hypotrail import, assess and compare each run with the Control, as a subprocess, and reads back only the aggregates of the dev Comparison (`comparison.delta`); records each choice as `PROXY` under `keep-higher-dev-score@1`; parks on a stop or a limit, with a note | Runs the held-out set; opens a Study file or a References file; runs `hypotrail decide`; makes a live call; resumes after a stop; raises its own budget; writes `HUMAN` |
| **Evaluator job** (`evaluator.py`, `evaluator_job.py`, `.github/workflows/evaluator.yml`) | The only place that runs the held-out set: it reads one frozen Configuration, runs the harness on it, exports, and runs Hypotrail's `init`, `import agent`, `assess --scorer text_facts_v1` and `report` in the job; it publishes an aggregate report only | Shows per-Case answers, questions or References to an agent session; runs from a local session; spends beyond its own ceiling |
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

### 2.4 Repeats, and the `failed` and `missing` lines

- `--repeats N` (default 1) runs every Case N times, `repeat` 0 to N-1; the pairs are listed Case by Case. Below 1 is invalid input (exit 2), refused before anything is recorded. `agentkit export --run` takes no `--repeats`: the run's pairs are the ones it recorded.
- A pair the adapter answers `failed` keeps its line, with the reason in `metadata.reason`. A `failed` answer without `metadata.reason` is refused: a fixture line is refused when the fixture is read (exit 2, nothing recorded), and any other adapter's answer closes its run `failed` without a reason, which is not exported (exit 2). A pair the run did not reach (a ceiling, a stop) is a `missing` line whose `metadata.reason` is the reason the run stopped. A pair the scripted fixture holds no answer for (any `repeat` but 0 of the twin's fixtures) is a `missing` line with `metadata.reason` `no_answer`, as Hypotrail counts an Episode its log does not hold. The scripted adapter never replays repeat 0's answer for another repeat: that would fake repeats and show a false zero variance. Repeated material will come from a separate authored fixture with three repeats, marked as authored (planned in Hypotrail's real-assistant thread) (drive, 2026-11-26). No pair of the population is dropped: `answers.jsonl` and `population.json` list the same pairs.
- In `usage`, a measure (`tokens`, `latency_seconds`, `cost_usd`) an entry does not give is exported `null`, never 0.

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
- The evaluator job, `agentkit evaluate --workload-dir DIR --candidate NAME --out OUT` in `.github/workflows/evaluator.yml`, is the one caller of the entry point. It reads one Configuration, a scripted fixture, on the held-out set. Its harness run, the run's answers, the export and Hypotrail's Study are in a temporary directory outside the kit's working directory, which it deletes when the reading ends, on an error or SIGTERM too; SIGKILL leaves it in `$TMPDIR`. The kit's ledger counts the reading in a record that lists no pair. Only `aggregate.json` (`kit-aggregate/1`) and `aggregate.md`, a short page made from it alone, are written in `OUT`, which must be empty or new; the workflow uploads them and adds the page to the job's summary. The job writes no decision, and no Comparison is made: a reading is of one Configuration. Outside a job of the workflow, `agentkit evaluate` exits 9 with a ledger record `refused`; a reading it cannot name in its report (no job commit, run or actor, no git checkout of Hypotrail, a candidate that is not a fixture name, an `OUT` that holds files, a fixture it cannot replay) exits 2 and records nothing. Once the reading is counted, any error, SIGINT or SIGTERM closes it `failed`, spent, with the reason `hypotrail` (a failing Hypotrail command, exit 1), `interrupted` or `error` (exit 1), and publishes nothing. What the job prints never names a held-out Case: Hypotrail's stdout and stderr are not echoed, only the command and its exit code; a fixture error is reported without its pair; any other error after the count by its type only, with no traceback.
- The conductor's Hypotrail subprocess reads the full References file, the held-out References included, inside a temporary Study outside the kit's working directory, which the conductor deletes when its loop ends; only the dev Comparison's delta is kept, under `ledger/conductor/` (section 7). The conductor itself reads back only that delta. This is a boundary in code between two processes of the same user, not a wall.
- On the twin, the held-out Cases and References are public files of a Hypotrail checkout. The kit demonstrates the pattern; it protects nothing that is not already public.
- A real wall comes from the place: the held-out set stored where only the evaluator job's identity can read it, and a job identity the agents do not have. The workflow runs in a protected GitHub environment with a required reviewer; the storage and the identities are the deploying team's to provide.

## 4. The decision log

`decisions/log.jsonl`, one JSON object per decision, in the form `kit-decision/1` (`contracts/kit-decision-1/`; it adds `schema_version` to the fields below):

| Field | Value |
|---|---|
| `id`, `time` | A sequence id and a UTC timestamp |
| `question`, `option` | What was decided, and the option kept |
| `mark` | `HUMAN`, `RELAYED` or `PROXY` |
| `rule` | Required for `PROXY` (the rule applied by name and version) and `RELAYED` (`owner-relayed@1`) |
| `words` | The person's words from their file for `HUMAN`. The owner's exact words from the cited message for `RELAYED` |
| `relayed_by` | Required for `RELAYED`: the name of the copying agent |
| `source` | For `RELAYED`, the HTTPS URL of the message. Otherwise, an object of `run`, `stop` and `pull_request` (a pull request as `https://github.com/<owner>/<repo>/pull/<n>@<40-hex commit>`: its merge commit, or the head it was reviewed at, as K-0001 cites) |
| `by` | The process that wrote the line (`conductor`, `cli`, `evaluator`) |

- A `PROXY` line without a `rule`, or a `HUMAN` line without `words`, is refused.
- The conductor's and evaluator's processes refuse `HUMAN` and `RELAYED`. The writing process is named by `AGENTKIT_PROCESS`, which the conductor and the evaluator entry point set for themselves and their children; unset, it is `cli`. A `HUMAN` line is written only by `agentkit decide --mark human --words-file FILE` in an unmarked process. This is a refusal in code, testable. It is a guard against an accidental `HUMAN` write, not a wall: a child process can reset the variable, and any unmarked shell counts as `cli` (K-0002). While the conductor's loop runs, its marker file also refuses `decide --mark human` and `decide --mark relayed`, whatever `AGENTKIT_PROCESS` says (section 7). It is a guard too: not a wall, not authentication. A `HUMAN` or `PROXY` decision cites a run of the ledger (`--run`), a stop file (`--stop`) or a pull request (`--pull-request`); anything else is refused (exit 2). The ids are `K-0001`, `K-0002`, and so on.
- `HUMAN` means "a person's words were transcribed"; it does not authenticate anyone. The authenticated act is a pull request merged from a human account. The log says which.
- `agentkit cite ID` prints the line's citation (`kit-citation/1`), which a Hypotrail Decision may hold as its `source`. It is refused (exit 2) unless the line is, as it is now, in the log at the commit checked out. Its level is derived, never stored: `pr` for a `HUMAN` line that cites a pull request in that form (cited and form-checked offline, never verified with the forge: nothing checks that the pull request exists, was merged or holds that commit), `relayed` for any other `HUMAN` line or any `RELAYED` line, `none` for a `PROXY` line.
- [D-215](https://github.com/chadlis/hypotrail/issues/75#issuecomment-7223332432) ranks evidence as `HUMAN > RELAYED > PROXY`. `HUMAN` has the person's words from their file. `RELAYED` preserves the exact words from a cited owner message and identifies the copying agent. `PROXY` applies a rule.
- `agentkit decide --mark relayed --words TEXT --source HTTPS_URL --relayed-by NAME` records the relayed words with the fixed rule. Empty words or agent names, missing sources and invalid HTTPS URLs are refused. The offline writer checks the format without authenticating the speaker or checking the quote against the URL. The opt-in online CI check looks up only reviewed public decision-log comments and verifies the exact quote. It is skipped in the ordinary offline suite.
- `agentkit status --json` reports decision marks and levels as `kit-status/1`. Agentkit's gate field is null. The separate MLkit phase gate accepts `HUMAN` or `RELAYED`. Agentkit stops continue to require `HUMAN`.
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
| `held_out_readings` | An integer: held-out runs allowed per Configuration (the run's `settings`); a held-out run counts unless it was `refused`. The count reads the ledger of the working directory, so it holds where that ledger is kept: a job's runner starts with an empty one. Across job runs it is not enforced yet; chadlis/agentkit#25 tracks it and must land before the first real dispatch of `evaluator.yml`, and the job's Decision is chadlis/agentkit#26. The kit log line for these choices is held for the owner (2026-11-27) |
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
| 2 | Invalid input (a Case file, a fixture, a budget file, a stops file, a refused decision) |
| 3 | A stop rule fired on this run: a stop file is written |
| 5 | An open stop blocks new runs |
| 7 | Gate: a blocking question has no `HUMAN` answer |
| 8 | Budget: this run would exceed a limit |
| 9 | Held-out refused outside the evaluator job |

`stops.json`, versioned with the code and read from the working directory as `budget.json` is, holds the stop rules (K-0001, a `PROXY` decision under D-166). Both apply to dev runs only:

| Rule | Fires on |
|---|---|
| `budget-reached@1` | The dev run that spends the last of the `dev_runs` of `budget.json` |
| `failed-share@1` | A dev run whose `failed` lines are more than the share `above` (0.2) of its `produced` and `failed` lines. `missing` lines do not count: the scripted repeats beyond a fixture are `missing` by design (section 2.4) |

- A rule is checked on a run the harness completes. A run it stops keeps its exit: 8 at a live ceiling, 1 on a provider error. It fires no rule.
- A fired rule writes `stops/STOP-<run>.json` (`id`, `rule`, `rules`, `run`, `time`), the ledger record of the run names the stop, and `agentkit run` exits 3. `agentkit export` with the options of a run exports it, then exits 3.
- While a stop is open, every run exits 5 with a ledger record `refused` whose `reason` is `stop_open`, and calls no adapter. This is checked after the held-out refusal and before the budget.
- A missing or malformed `stops.json` is invalid input (exit 2).

A stop is resumed only by a `HUMAN` decision that cites it in `source.stop`; a `PROXY` decision that cites it resumes nothing. In automatic mode, a stop parks the conductor and leaves a note for the person.

## 7. Driving the kit

**Accompanied.** The person works in a chat session (the agent guide) with the kit's repository open. The guide reads `agentkit status` before proposing a run, says what it costs in budget, and runs only what the person asks. After a run it points to the ledger record and the exported files; it shows numbers where they are written and does not retype them. The person imports the export into a dev Study of Hypotrail and decides there, in their own words. The guide says who made each choice: the person, a `PROXY` rule, or the conductor.

**The guide's JSON contract (C6).** Commands on the guide surface with `--json` return one object with `ok` (boolean success), `code` (a machine-readable string, `OK` for success), `exit` (the integer process exit status), `message` (a human-readable string) and `details` (an object). A successful `status --json` has `kit-status/1` in `details`. On refusal the guide reads `code` for the command's repository because exit numbers can mean different things across kits. Agentkit currently prints human-readable status. Its `--json` status and `docs/guide-surface.json` are deliverables of the status ticket.

**Automatic.** The conductor runs alone on the dev set: it tries Configurations within the dev budget, records each rule-based choice as `PROXY`, and parks on any stop, gate or budget limit. It never touches the held-out set. The details follow, under "The conductor". When the person returns, the agent guide reads the ledger and the log and tells what happened: runs spent, `PROXY` decisions, parked stops, what waits for them. The guide does not iterate in this mode.

**The held-out pass**, in both modes, is the evaluator job, started by a person: a manual dispatch of `evaluator.yml` with the candidate's name, in the protected environment `held-out` (section 3).

### The conductor

`agentkit conduct --workload-dir DIR --set dev --configurations A,B` (K-0003, a `PROXY` decision under D-166):

- A Configuration is the name of a scripted fixture (`fixtures/answers-NAME.jsonl`). The conductor makes no live call: `--adapter live` is refused with exit 2, and nothing is recorded. A name list that is not distinct fixture names is refused with exit 2 too.
- It runs each Configuration once, in order, through the harness, so every run is counted, budgeted and checked against the stops as `agentkit run` is. It exports each completed run and has Hypotrail `init` (once), `import agent` and `assess --scorer text_facts_v1` it, in a dev Study in a temporary directory outside the kit's working directory, with the exports it imports. Hypotrail runs as a subprocess. `hypotrail init` copies the whole References file into the Study, the held-out References included, so the conductor deletes the directory when the loop ends, on an error, SIGINT or SIGTERM too. SIGKILL or a power loss leaves the temporary Study in `$TMPDIR`, outside the kit, and a stale marker. It keeps only each dev Comparison's delta, with the two Configurations and their runs, in `ledger/conductor/<conduct_id>-comparisons.jsonl`.
- The first Configuration is the Control. Each later one is compared with the Control (`hypotrail compare`), and `keep-higher-dev-score@1` applied to the two shares of `comparison.delta` gives one `PROXY` line, which cites the Candidate's run. The Configuration it keeps is the Control of the next Comparison. Of what Hypotrail prints, the conductor reads only the ids it passes on and that delta. It never opens a Study file or a References file, never runs `hypotrail decide`, and nothing of the held-out set is left in the kit's working directory.
- It parks on the first of: a stop its run fires (exit 3), an open stop (exit 5), a budget limit (exit 8), the held-out set (exit 9, checked before a fixture is read). A failing Hypotrail command ends the loop with exit 1. It never resumes a stop and never edits `budget.json`.
- When the loop ends, on an error, SIGINT or SIGTERM too, it writes `ledger/conductor/<conduct_id>-note.md` for the person: the runs spent, the `PROXY` decisions, and the stop or limit that parked it, or that it is done.

**The marker file** (chadlis/agentkit#22; drive, 2026-11-27, delegated and reversible):

1. It is built with the conductor.
2. It covers the whole `conduct` loop. The conductor writes `ledger/conductor/<conduct_id>.json` (its pid and start time) when the loop starts, and removes it when the loop ends, on an error, SIGINT or SIGTERM too; SIGKILL or a power loss leaves it, stale.
3. `agentkit decide` with `--mark human` or `--mark relayed` refuses (exit 2) while any marker names a live pid, whatever `AGENTKIT_PROCESS` says, so a child of the conductor that resets the variable is refused while the loop runs. The conductor resolves its marker directory once, as an absolute path, and names it in `AGENTKIT_CONDUCTOR_DIR` for its children, so that a child started in another directory looks there as well as in its own `ledger/conductor/`. A stale marker, whose pid is gone, is reported on stderr and ignored. A marker that cannot be read refuses both marks.
4. It is a guard against an accidental `HUMAN` write, not a wall, not authentication: a process can delete the file; a child that both resets `AGENTKIT_CONDUCTOR_DIR` and runs in another directory, or a process the conductor did not start, sees no marker outside its own working directory; a reused pid reads as live; and a person's process in the same working directory at the same time is refused as well.
