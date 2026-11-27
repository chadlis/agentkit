# Contracts

The file formats between this kit, chadlis/mlkit and chadlis/hypotrail (D-172 on chadlis/hypotrail#75). Each contract has one owner repository, which holds its JSON Schema and examples; every other side holds a byte-identical copy and a line in `contracts.lock.json`.

| Directory | Contract | Owner | Here |
|---|---|---|---|
| `agent-answers-1/` | `agent-answers/1`: `answers.jsonl` and `population.json` for `hypotrail import agent` | chadlis/hypotrail | copy |
| `kit-decision-1/` | `kit-decision/1`, `kit-citation/1`: the decision log and its citation | chadlis/agentkit | owner |
| `kit-aggregate-1/` | `kit-aggregate/1`: the evaluator job's report | chadlis/agentkit | owner |
| `kit-status-1/` | `kit-status/1`: the details of a successful `status --json` response | chadlis/agentkit | owner |

**`contracts.lock.json`.** One entry per copied directory: `name`, `version`, `owner`, `owner_commit` (the owner's commit the copy was taken from) and `sha256`. The digest is the sha256 of the lines `<sha256 of the file>  <path in the directory>`, one per file, sorted by path, each ending in a newline: what `sha256sum` prints for the sorted file list. A test recomputes it, so a copy edited here fails.

**Changes.** The owner logs each change in `docs/contracts/CHANGELOG.md`. A copy moves only in a contract-pin pull request that updates the copy, the lock and, once it exists, `hypotrail.pin`, and whose end-to-end job is green. Until the owners' first contract pull requests merge, `owner_commit` is a commit of their `claude/contracts` branch; the first contract-pin pull request moves it to the merge commit.
