"""The decision log, `decisions/log.jsonl` of the working directory, in the form `kit-decision/1`, and its citation `kit-citation/1` (`contracts/kit-decision-1/`).

The writing process is named by `AGENTKIT_PROCESS`: unset or `cli` for the `agentkit` command, `conductor` or `evaluator` once `enter_process` has marked a process. The mark is inherited by every child process, and a marked process cannot unmark itself. A line's level is derived from its mark and source when it is read, never stored."""

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from agentkit import ledger

LOG = Path("decisions") / "log.jsonl"
STOPS = Path("stops")
KIT = "chadlis/agentkit"
SCHEMA_VERSION = "kit-decision/1"
PROCESS_VARIABLE = "AGENTKIT_PROCESS"
PROCESSES = ("cli", "conductor", "evaluator")
RULE = re.compile(r"[a-z0-9_-]+@[0-9]+")
PULL_REQUEST = re.compile(r"https://github\.com/[^/]+/[^/]+/pull/[0-9]+@[0-9a-f]{40}")
ID = re.compile(r"K-[0-9]{4,}")


class DecisionError(ValueError):
    """A decision, or a citation of one, is refused: nothing is written. `code` names the refusal."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def process() -> str:
    """The process that writes: `cli` unless a mark says otherwise. An unknown mark is refused rather than read as `cli`."""
    role = os.environ.get(PROCESS_VARIABLE) or "cli"
    if role not in PROCESSES:
        raise DecisionError("PROCESS_UNKNOWN", f"{PROCESS_VARIABLE}={role} names no writer of the log: {', '.join(PROCESSES)}")
    return role


def enter_process(role: str) -> None:
    """Marks this process and its children as `role`. A process marked `conductor` or `evaluator` stays so."""
    current = process()
    if current != "cli" and current != role:
        raise DecisionError("PROCESS_MARKED", f"this process is the {current}'s and cannot become the {role}'s")
    os.environ[PROCESS_VARIABLE] = role


def lines(log: Path = LOG) -> list[dict]:
    """The lines of the log, in the order they were written; none when it does not exist. Reads only."""
    if not log.is_file():
        return []
    try:
        return [json.loads(text) for text in log.read_text(encoding="utf-8").splitlines() if text.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DecisionError("LOG_UNREADABLE", f"{log}: {error}") from error


def level(line: dict) -> str:
    """`pr` for a `HUMAN` line whose `source.pull_request` has the form `https://github.com/<owner>/<repo>/pull/<n>@<40-hex commit>`, `relayed` for any other `HUMAN` line, `none` for a `PROXY` line."""
    if line["mark"] != "HUMAN":
        return "none"
    cited = line["source"].get("pull_request")
    return "pr" if isinstance(cited, str) and PULL_REQUEST.fullmatch(cited) is not None else "relayed"


def _source(source: dict) -> dict:
    source = {key: value for key, value in source.items() if value is not None}
    if not source:
        raise DecisionError("SOURCE_MISSING", "a decision cites a run, a stop or a pull request: give --run, --stop or --pull-request")
    if "run" in source and source["run"] not in {record["run_id"] for record in ledger.read_records()}:
        raise DecisionError("RUN_UNKNOWN", f"{source['run']} is not in {ledger.runs_file(ledger.LEDGER_DIR)}")
    if "stop" in source and not (STOPS / f"{source['stop']}.json").is_file():
        raise DecisionError("STOP_UNKNOWN", f"no stop file {STOPS / source['stop']}.json")
    if "pull_request" in source and PULL_REQUEST.fullmatch(source["pull_request"]) is None:
        raise DecisionError("PULL_REQUEST_FORM", "a pull request is cited as https://github.com/<owner>/<repo>/pull/<n>@<40-hex merge commit>")
    return source


def _words(words_file: Path | None) -> str:
    if words_file is None:
        raise DecisionError("HUMAN_WITHOUT_WORDS", "a HUMAN decision needs --words-file: the person's own words, in a file the person wrote")
    try:
        words = words_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DecisionError("WORDS_UNREADABLE", f"{words_file} cannot be read as UTF-8 text ({error})") from None
    if not words.strip():
        raise DecisionError("HUMAN_WITHOUT_WORDS", f"{words_file} holds no words")
    return words


def append(*, question: str, option: str, mark: str, source: dict, by: str, rule: str | None = None, words_file: Path | None = None, log: Path = LOG) -> dict:
    """Appends one line to the log and returns it. `HUMAN` is refused unless `by` is `cli` and this process is unmarked."""
    if not question.strip() or not option.strip():
        raise DecisionError("DECISION_EMPTY", "a decision names its question and the option kept")
    if by not in PROCESSES:
        raise DecisionError("PROCESS_UNKNOWN", f"{by} names no writer of the log: {', '.join(PROCESSES)}")
    if mark == "PROXY":
        if rule is None:
            raise DecisionError("PROXY_WITHOUT_RULE", "a PROXY decision names its rule: --rule NAME@VERSION")
        if words_file is not None:
            raise DecisionError("PROXY_WITH_WORDS", "a PROXY decision holds a rule, not a person's words")
        if RULE.fullmatch(rule) is None:
            raise DecisionError("RULE_FORM", f"{rule} is not NAME@VERSION (lower case, digits, - and _)")
    elif mark == "HUMAN":
        writer = by if by != "cli" else process()
        if writer != "cli":
            raise DecisionError("HUMAN_REFUSED", f"the {writer}'s process cannot write HUMAN")
        if rule is not None:
            raise DecisionError("HUMAN_WITH_RULE", "a HUMAN decision holds the person's words, not a rule")
    else:
        raise DecisionError("MARK_UNKNOWN", f"{mark} is neither HUMAN nor PROXY")
    source = _source(source)
    held = {"rule": rule} if mark == "PROXY" else {"words": _words(words_file)}
    number = max((int(line["id"][2:]) for line in lines(log)), default=0) + 1
    line = {
        "schema_version": SCHEMA_VERSION,
        "id": f"K-{number:04d}",
        "time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "question": question,
        "option": option,
        "mark": mark,
        **held,
        "source": source,
        "by": by,
    }
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def cite(decision_id: str, log: Path = LOG) -> str:
    """The `kit-citation/1` line of a decision, at the commit checked out in the working directory, which must hold that line as it is now."""
    if ID.fullmatch(decision_id) is None:
        raise DecisionError("DECISION_ID_FORM", f"{decision_id} is not K-NNNN")
    found = [line for line in lines(log) if line["id"] == decision_id]
    if not found:
        raise DecisionError("DECISION_UNKNOWN", f"{decision_id} is not in {log}")
    [line] = found
    head = _git("rev-parse", "HEAD")
    commit = head.stdout.strip()
    if head.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise DecisionError("KIT_COMMIT_UNKNOWN", "the working directory has no commit checked out: a citation names the kit commit")
    shown = _git("show", f"{commit}:{log.as_posix()}")
    at_commit = [json.loads(text) for text in shown.stdout.splitlines() if text.strip()] if shown.returncode == 0 else []
    if line not in at_commit:
        raise DecisionError("CITED_LINE_NOT_COMMITTED", f"{decision_id} is not at commit {commit} as it is in the log: commit the log, then cite")
    return f"kit:{KIT}@{commit}:{log.as_posix()}#{decision_id};mark={line['mark']};level={level(line)}"
