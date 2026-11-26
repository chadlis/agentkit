# Agent instructions

## Rules

- Everything committed to this repository is in English: code, comments, docs, file names, commit messages, issues and pull requests.
- `main` changes only through a pull request; agents may open and merge it.
- Live LLM calls are opt-in and bounded: each one needs an explicit flag, a key in the environment and a ceiling (calls, tokens, spend) checked before the first call. None runs in CI or in a test.
- No private content: no company, client, person or private figure, and no data other than the public twin's. Patterns are described in generic terms.
- Reference answers are never shown to an evaluated agent, and a Reference is never changed to improve a score.
- The held-out set is read only inside the evaluator job (`docs/design.md`, "The wall"). An agent session never runs it, reads its Cases or References, or reads per-Case results of it.
- An agent never writes a `HUMAN` mark in the decision log. A rule-based decision is `PROXY` and names its rule.
- Leave the software license choice to the owner.
- Keep `README.md` short and true: update it in the same pull request when a change affects what it says (purpose, status, install, usage, layout or where to look).

## Vocabulary

This kit feeds Hypotrail and uses its words (Case, Repeat, Partition, Reference, Import, Observation set, Assessment): see `GLOSSARY.md` in chadlis/hypotrail. `docs/design.md` is the contract of this kit.

## Issue tracker

GitHub Issues on chadlis/agentkit (`gh` locally; `gh api repos/chadlis/agentkit/...` in cloud sessions). Pass the repository explicitly: never infer it from `origin`.
