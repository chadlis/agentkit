"""The end-to-end test of chadlis/agentkit#2 (D-170 on chadlis/hypotrail#75): the kit exports the twin's dev set with the scripted adapter, and Hypotrail at the pinned commit imports, assesses, compares and records a proxy Decision whose source is the `agentkit cite` line of the kit's own `PROXY` decision (C4 of D-172).

It runs the real commands, `agentkit` and `python -m hypotrail`, each in its own process. A `sitecustomize` put on their `PYTHONPATH` records every open of the References file by the kit and every socket call by any of them. Offline; no model call.
"""

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from agentkit import harness
from agentkit.adapters import RunStopped
from agentkit.adapters.scripted import Answer, ScriptedAdapter
from agentkit.rules import KEEP_HIGHER_DEV_SCORE, keep_higher_dev_score

RULE = KEEP_HIGHER_DEV_SCORE
ROOT = Path(__file__).resolve().parents[2]
CITATION = re.compile(r"kit:chadlis/agentkit@(?P<commit>[0-9a-f]{40}):decisions/log\.jsonl#(?P<id>K-[0-9]{4,});mark=PROXY;level=none")
SITECUSTOMIZE = '''
import os, sys

_log = os.environ["AGENTKIT_AUDIT_LOG"]
_watched = os.environ.get("AGENTKIT_AUDIT_WATCH")


def _write(text):
    with open(_log, "a", encoding="utf-8") as log:
        log.write(text + "\\n")


def _hook(event, args):
    if event in ("socket.connect", "socket.getaddrinfo"):
        _write(f"{event} {args[1:]!r}")
    elif event == "open" and _watched and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.path.realpath(os.fsdecode(args[0])) == _watched:
            _write(f"open {_watched}")


sys.addaudithook(_hook)
'''


def agentkit_command() -> list[str]:
    script = Path(sys.executable).with_name("agentkit")
    assert script.is_file(), f"the agentkit command is not installed next to {sys.executable}"
    return [str(script)]


def hypotrail_command() -> list[str]:
    found = subprocess.run([sys.executable, "-c", "import hypotrail"], capture_output=True)
    if found.returncode != 0:
        message = "Hypotrail is not installed in this environment: uv pip install ./hypotrail"
        if os.environ.get("AGENTKIT_REQUIRE_TWIN") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return [sys.executable, "-m", "hypotrail"]


class Commands:
    def __init__(self, tmp_path: Path, references: Path, cwd: Path | None = None):
        self.audit = tmp_path / "audit"
        self.audit.mkdir()
        (self.audit / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        self.references = os.path.realpath(references)
        self.cwd = tmp_path if cwd is None else cwd

    def run(self, command: list[str], log: str, watch_references: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != "HYPOTRAIL_ANTHROPIC_API_KEY"} | (env or {})
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(self.audit), env.get("PYTHONPATH")]))
        env["AGENTKIT_AUDIT_LOG"] = str(self.audit / f"{log}.log")
        if watch_references:
            env["AGENTKIT_AUDIT_WATCH"] = self.references
        return subprocess.run(command, cwd=self.cwd, env=env, capture_output=True, text=True, timeout=600)

    def log(self, name: str) -> list[str]:
        path = self.audit / f"{name}.log"
        return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def git(work: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=agentkit-e2e", "-c", "user.email=e2e@example.invalid", *args], cwd=work, capture_output=True, text=True, check=True)
    return done.stdout


def kit_clone(tmp_path: Path) -> Path:
    """A scratch clone of the kit, whose commits a citation of its decision log can name; it holds the versioned `budget.json` and `stops.json`."""
    work = tmp_path / "agentkit"
    subprocess.run(["git", "clone", "--quiet", str(ROOT), str(work)], check=True)
    return work


def json_out(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_the_dev_export_of_both_fixtures_is_imported_assessed_compared_and_decided_by_hypotrail(workload_dir, tmp_path):
    hypotrail = hypotrail_command()
    examples = str(workload_dir.parent)
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    work = kit_clone(tmp_path)
    commands = Commands(tmp_path, workload_dir / workload["references"], cwd=work)
    study = str(tmp_path / "study")

    exports = {}
    for fixture in ("concise", "verbose"):
        out = tmp_path / f"export-{fixture}"
        result = commands.run(
            [*agentkit_command(), "export", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", fixture, "--out", str(out)],
            f"agentkit-export-{fixture}",
            watch_references=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        exports[fixture] = out

    init = json_out(commands.run([*hypotrail, "init", "--workload", "chinook_prose", "--study", study, "--examples-dir", examples, "--json"], "hypotrail-init"))
    references_id = init["references_id"]

    observation_sets = {}
    for fixture, out in exports.items():
        result = commands.run(
            [*hypotrail, "import", "agent", "--study", study, "--source", str(out / "answers.jsonl"), "--population", str(out / "population.json"),
             "--request-id", f"import-{fixture}", "--examples-dir", examples],
            f"hypotrail-import-{fixture}",
        )
        assert "30 Episodes produced, 0 failed, 0 missing" in result.stdout, result.stdout + result.stderr
        assert result.returncode == 0, result.stderr
        observation_sets[fixture] = result.stdout.rsplit("Observation set ", 1)[1].strip()

    assessments = {}
    for fixture, observation_set in observation_sets.items():
        assessed = json_out(commands.run(
            [*hypotrail, "assess", "--study", study, "--observations", observation_set, "--scorer", "text_facts_v1",
             "--references", references_id, "--examples-dir", examples, "--json"],
            f"hypotrail-assess-{fixture}",
        ))
        assessments[fixture] = assessed["assessment"]["assessment_id"]
    assert len(set(assessments.values())) == 2

    comparison = json_out(commands.run(
        [*hypotrail, "compare", "--study", study, "--control", assessments["concise"], "--candidate", assessments["verbose"], "--examples-dir", examples, "--json"],
        "hypotrail-compare",
    ))["comparison"]
    comparison_id = comparison["comparison_id"]

    status = json_out(commands.run([*hypotrail, "status", "--study", study, "--json"], "hypotrail-status-before"))
    assert [item["comparison_id"] for item in status["comparisons"]] == [comparison_id]
    for fixture, observation_set in observation_sets.items():
        exported = json.loads((exports[fixture] / "answers.jsonl").read_text(encoding="utf-8").splitlines()[0])["settings"]
        attempt = next(item for item in status["attempts"] if item["request_id"] == f"import-{fixture}")
        recorded = json.loads((Path(study) / "attempts" / attempt["attempt_id"] / "observation_set.json").read_text(encoding="utf-8"))
        assert recorded["observation_set_id"] == observation_set
        assert recorded["provenance"]["settings"] == {"origin": "declared", "value": exported}, fixture

    pending = next(item for item in status["pending_decisions"] if item["comparison_id"] == comparison_id)
    delta = comparison["delta"]
    option_id = keep_higher_dev_score(control=delta["control"], candidate=delta["candidate"])
    option = next(item for item in pending["options"] if item["id"] == option_id)
    question = pending["question"]["text"]
    kit_decided = commands.run(
        [*agentkit_command(), "decide", "--mark", "proxy", "--rule", RULE, "--question", question, "--option", option["text"], "--run", "r-0002"],
        "agentkit-decide",
    )
    assert kit_decided.returncode == 0, kit_decided.stdout + kit_decided.stderr
    log = work / "decisions" / "log.jsonl"
    kit_line = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert (kit_line["mark"], kit_line["rule"], kit_line["by"], kit_line["source"]) == ("PROXY", RULE, "cli", {"run": "r-0002"})
    git(work, "add", "decisions/log.jsonl")
    git(work, "commit", "--quiet", "-m", f"{kit_line['id']}: the end-to-end test's PROXY decision")

    words = tmp_path / "words.txt"
    words.write_text("Example words written for this end-to-end test, not a person's: keep it.\n", encoding="utf-8")
    before = log.read_bytes()
    conductor = commands.run(
        [*agentkit_command(), "decide", "--mark", "human", "--words-file", str(words), "--question", question, "--option", option["text"], "--run", "r-0002"],
        "agentkit-decide-conductor", env={"AGENTKIT_PROCESS": "conductor"},
    )
    assert conductor.returncode == 2, conductor.stdout + conductor.stderr
    assert log.read_bytes() == before

    cited = commands.run([*agentkit_command(), "cite", kit_line["id"]], "agentkit-cite")
    assert cited.returncode == 0, cited.stderr
    citation = cited.stdout.removesuffix("\n")
    parts = CITATION.fullmatch(citation)
    assert parts and parts["id"] == kit_line["id"], citation
    at_commit = [json.loads(text) for text in git(work, "show", f"{parts['commit']}:decisions/log.jsonl").splitlines() if text.strip()]
    assert [line for line in at_commit if line["id"] == kit_line["id"]] == [kit_line]

    decided = json_out(commands.run(
        [*hypotrail, "decide", "--study", study, "--author", "proxy",
         "--question", question, "--option", option["text"],
         "--question-id", pending["question"]["id"], "--option-id", option_id,
         "--artifact", comparison_id, "--read-sequence", str(status["write_sequence"]),
         "--source", citation,
         "--request-id", "decide-keep", "--json"],
        "hypotrail-decide",
    ))["decision"]
    assert pending["question"]["id"] == "keep_after_comparison"
    assert decided["author"] == "proxy" and decided["cited_ids"] == [comparison_id]
    assert decided["source"].encode("utf-8") == cited.stdout.encode("utf-8").removesuffix(b"\n")

    after = json_out(commands.run([*hypotrail, "status", "--study", study, "--json"], "hypotrail-status-after"))
    assert [(d["decision_id"], d["applies"]) for d in after["decisions"]] == [(decided["decision_id"], True)]
    assert [item["comparison_id"] for item in after["pending_decisions"]] == []

    kit_lines = commands.log("agentkit-export-concise") + commands.log("agentkit-export-verbose")
    assert [line for line in kit_lines if line.startswith("open ")] == []
    sockets = [line for path in sorted(commands.audit.glob("*.log")) for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("socket.")]
    assert sockets == []


def import_counts(commands: Commands, hypotrail: list[str], workload_dir: Path, tmp_path: Path, out: Path) -> subprocess.CompletedProcess:
    """Imports the export in `out` into a new Study of `chinook_prose`; returns the import's process."""
    examples = str(workload_dir.parent)
    study = str(tmp_path / "study")
    json_out(commands.run([*hypotrail, "init", "--workload", "chinook_prose", "--study", study, "--examples-dir", examples, "--json"], "hypotrail-init"))
    return commands.run(
        [*hypotrail, "import", "agent", "--study", study, "--source", str(out / "answers.jsonl"), "--population", str(out / "population.json"),
         "--request-id", "import", "--examples-dir", examples],
        "hypotrail-import",
    )


def test_three_repeats_of_the_dev_export_reach_hypotrail_as_one_episode_per_case_and_repeat(workload_dir, tmp_path):
    hypotrail = hypotrail_command()
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    commands = Commands(tmp_path, workload_dir / workload["references"])
    out = tmp_path / "export"
    result = commands.run(
        [*agentkit_command(), "export", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", "concise",
         "--repeats", "3", "--out", str(out)],
        "agentkit-export",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [json.loads(text) for text in (out / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    counts = Counter(line["observation_status"] for line in lines)
    # The fixture holds an answer for repeat 0 only: repeats 1 and 2 are pairs it does not hold, hence missing.
    assert (len(lines), counts) == (90, {"produced": 30, "missing": 60})
    imported = import_counts(commands, hypotrail, workload_dir, tmp_path, out)
    assert "30 Episodes produced, 0 failed, 60 missing" in imported.stdout, imported.stdout + imported.stderr
    assert imported.returncode == 0, imported.stderr


class FailsOnceThenStops:
    """Replays the concise fixture, except a `failed` answer for one dev Case and a stop of the run at a later one."""

    name = "fails-once-then-stops"

    def __init__(self, workload_dir: Path, fails_on: str, stops_on: str):
        self._inner = ScriptedAdapter(workload_dir / "fixtures" / "answers-concise.jsonl")
        self.settings = self._inner.settings
        self.fails_on, self.stops_on = fails_on, stops_on

    def answer(self, case, repeat):
        if case.case_id == self.stops_on:
            raise RunStopped("test_stop", f"the test stops the run at {case.case_id}")
        if case.case_id == self.fails_on:
            return Answer(fields={"observation_status": "failed", "metadata": {"reason": "adapter_error", "error": "the test adapter failed"}})
        return self._inner.answer(case, repeat)


def test_an_export_with_failed_and_missing_lines_is_imported_by_hypotrail_with_the_same_counts(workload_dir, tmp_path):
    hypotrail = hypotrail_command()
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    commands = Commands(tmp_path, workload_dir / workload["references"])
    # The run is made in this process, in the ledger of `tmp_path`, which the commands share as their working directory.
    run_id = harness.run(workload_dir, "dev", FailsOnceThenStops(workload_dir, fails_on="D05", stops_on="D28"))
    out = tmp_path / "export"
    result = commands.run([*agentkit_command(), "export", "--run", run_id, "--out", str(out)], "agentkit-export")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [json.loads(text) for text in (out / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    counts = Counter(line["observation_status"] for line in lines)
    assert counts == {"produced": 26, "failed": 1, "missing": 3}
    imported = import_counts(commands, hypotrail, workload_dir, tmp_path, out)
    assert f"{counts['produced']} Episodes produced, {counts['failed']} failed, {counts['missing']} missing" in imported.stdout, imported.stdout + imported.stderr
    assert imported.returncode == 0, imported.stderr
