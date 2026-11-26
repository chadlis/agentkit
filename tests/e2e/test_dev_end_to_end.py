"""The end-to-end test of chadlis/agentkit#2 (D-170 on chadlis/hypotrail#75): the kit exports the twin's dev set with the scripted adapter, and Hypotrail at the pinned commit imports, assesses, compares and records a proxy Decision.

It runs the real commands, `agentkit` and `python -m hypotrail`, each in its own process. A `sitecustomize` put on their `PYTHONPATH` records every open of the References file by the kit and every socket call by any of them. Offline; no model call.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RULE = "keep-higher-dev-score@1"
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
    def __init__(self, tmp_path: Path, references: Path):
        self.audit = tmp_path / "audit"
        self.audit.mkdir()
        (self.audit / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        self.references = os.path.realpath(references)
        self.cwd = tmp_path

    def run(self, command: list[str], log: str, watch_references: bool = False) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != "HYPOTRAIL_ANTHROPIC_API_KEY"}
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(self.audit), env.get("PYTHONPATH")]))
        env["AGENTKIT_AUDIT_LOG"] = str(self.audit / f"{log}.log")
        if watch_references:
            env["AGENTKIT_AUDIT_WATCH"] = self.references
        return subprocess.run(command, cwd=self.cwd, env=env, capture_output=True, text=True, timeout=600)

    def log(self, name: str) -> list[str]:
        path = self.audit / f"{name}.log"
        return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def json_out(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_the_dev_export_of_both_fixtures_is_imported_assessed_compared_and_decided_by_hypotrail(workload_dir, tmp_path):
    hypotrail = hypotrail_command()
    examples = str(workload_dir.parent)
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    commands = Commands(tmp_path, workload_dir / workload["references"])
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

    # The test applies the rule itself: the kit has no decision log yet.
    pending = next(item for item in status["pending_decisions"] if item["comparison_id"] == comparison_id)
    delta = comparison["delta"]
    assert delta["control"] != delta["candidate"]
    option_id = "keep_control" if delta["control"] > delta["candidate"] else "keep_candidate"
    option = next(item for item in pending["options"] if item["id"] == option_id)
    decided = json_out(commands.run(
        [*hypotrail, "decide", "--study", study, "--author", "proxy",
         "--question", pending["question"]["text"], "--option", option["text"],
         "--question-id", pending["question"]["id"], "--option-id", option_id,
         "--artifact", comparison_id, "--read-sequence", str(status["write_sequence"]),
         "--source", f"rule {RULE}: keep the Configuration with the higher dev text_facts_success",
         "--request-id", "decide-keep", "--json"],
        "hypotrail-decide",
    ))["decision"]
    assert pending["question"]["id"] == "keep_after_comparison"
    assert decided["author"] == "proxy" and decided["cited_ids"] == [comparison_id] and RULE in decided["source"]

    after = json_out(commands.run([*hypotrail, "status", "--study", study, "--json"], "hypotrail-status-after"))
    assert [(d["decision_id"], d["applies"]) for d in after["decisions"]] == [(decided["decision_id"], True)]
    assert [item["comparison_id"] for item in after["pending_decisions"]] == []

    assert commands.log("agentkit-export-concise") + commands.log("agentkit-export-verbose") == []
    sockets = [line for path in sorted(commands.audit.glob("*.log")) for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("socket.")]
    assert sockets == []
