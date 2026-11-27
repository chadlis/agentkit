"""The end-to-end tests of the conductor, chadlis/agentkit#8, and of its marker file, chadlis/agentkit#22.

`agentkit conduct` runs the scripted fixtures on the twin's dev set under the budget and the stops. It compares each run with the Control through Hypotrail at the pinned commit, which it runs as a subprocess, and writes one `PROXY` line per Comparison. It parks on a stop or a limit, with a note. Offline; no model call.

The words files here are written for the tests and are no person's words."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentkit import conductor, decisions
from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cli import main
from agentkit.ledger import read_records
from agentkit.rules import KEEP_HIGHER_DEV_SCORE
from test_budget import write_budget
from test_dev_end_to_end import agentkit_command, hypotrail_command

CONDUCTOR_DIR = Path("ledger") / "conductor"
SITECUSTOMIZE = '''
import os, sys

_log = os.environ["AGENTKIT_AUDIT_LOG"]
_references = os.environ["AGENTKIT_AUDIT_WATCH"]
_conductor = os.environ["AGENTKIT_AUDIT_CONDUCTOR"]


def _hook(event, args):
    if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)) or os.path.basename(sys.argv[0]) != "agentkit":
        return
    path = os.path.realpath(os.fsdecode(args[0]))
    inside = path.startswith(_conductor + os.sep) and "study" in os.path.relpath(path, _conductor).split(os.sep)
    if path == _references or inside:
        with open(_log, "a", encoding="utf-8") as log:
            log.write(f"open {path}\\n")


sys.addaudithook(_hook)
'''


def log_lines() -> list[dict]:
    return decisions.lines()


def words() -> Path:
    path = Path("a7-words.txt")
    path.write_text("Example words written for this test, not a person's: resume.\n", encoding="utf-8")
    return path


def conduct(workload_dir: Path, configurations: str, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """`agentkit conduct` in its own process, in the working directory of the test, with every socket call and every open of the References or of a Study file by the conductor's own process logged."""
    audit = Path("a7-audit")
    audit.mkdir(exist_ok=True)
    (audit / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    environment = {key: value for key, value in os.environ.items() if key != "HYPOTRAIL_ANTHROPIC_API_KEY"} | (env or {})
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [str(audit.resolve()), environment.get("PYTHONPATH")]))
    environment["AGENTKIT_AUDIT_LOG"] = str((audit / "opens.log").resolve())
    environment["AGENTKIT_AUDIT_WATCH"] = os.path.realpath(workload_dir / workload["references"])
    environment["AGENTKIT_AUDIT_CONDUCTOR"] = os.path.realpath(CONDUCTOR_DIR)
    command = [*agentkit_command(), "conduct", "--workload-dir", str(workload_dir), "--set", "dev", "--configurations", configurations, *extra]
    return subprocess.run(command, env=environment, capture_output=True, text=True, timeout=600)


def opened() -> list[str]:
    path = Path("a7-audit") / "opens.log"
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def note(conduct_id: str = "c-0001") -> str:
    return (CONDUCTOR_DIR / f"{conduct_id}-note.md").read_text(encoding="utf-8")


def markers() -> list[Path]:
    return sorted(CONDUCTOR_DIR.glob("*.json"))


def human_lines() -> list[dict]:
    return [line for line in log_lines() if line["mark"] == "HUMAN"]


def test_conducting_two_configurations_closes_two_runs_and_writes_one_proxy_line_with_its_rule_from_the_dev_comparison(workload_dir):
    hypotrail_command()
    done = conduct(workload_dir, "concise,verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert human_lines() == []
    assert [(record["run_id"], record["set"], record["status"]) for record in read_records()] == [("r-0001", "dev", "completed"), ("r-0002", "dev", "completed")]
    [line] = log_lines()
    assert (line["mark"], line["rule"], line["by"], line["source"]) == ("PROXY", KEEP_HIGHER_DEV_SCORE, "conductor", {"run": "r-0002"})
    # On the twin, the concise fixture scores 1.0 and the verbose one 2/3 of dev text_facts_success: the Control is kept.
    assert line["option"] == "keep_control: concise"
    written = note()
    assert all(text in written for text in ("r-0001", "r-0002", line["id"]))
    assert markers() == []
    assert opened() == []


def test_a_budget_of_one_dev_run_parks_after_the_first_with_exit_8_and_a_note_naming_the_limit(workload_dir):
    hypotrail_command()
    write_budget(dev_runs=1)
    # Without budget-reached@1, which would fire on that first run and park it with exit 3 instead.
    Path("stops.json").write_text(json.dumps({"reason": "a7: failed-share@1 only", "rules": {"failed-share@1": {"above": 0.2}}}), encoding="utf-8")
    done = conduct(workload_dir, "concise,verbose")
    assert done.returncode == 8, done.stdout + done.stderr
    assert human_lines() == []
    assert [(record["status"], record.get("reason")) for record in read_records()] == [("completed", None), ("refused", "dev_runs")]
    assert "dev_runs" in note()
    assert markers() == []


def test_a_stop_rule_that_fires_parks_the_conductor_with_exit_3_and_a_second_conduct_exits_5(workload_dir):
    hypotrail_command()
    write_budget(dev_runs=2)
    first = conduct(workload_dir, "concise,verbose")
    assert first.returncode == 3, first.stdout + first.stderr
    assert (Path("stops") / "STOP-r-0002.json").is_file()
    assert "STOP-r-0002" in note("c-0001")
    second = conduct(workload_dir, "concise,verbose")
    assert second.returncode == 5, second.stdout + second.stderr
    assert "STOP-r-0002" in note("c-0002")
    assert human_lines() == []
    assert [(record["status"], record.get("reason")) for record in read_records()] == [("completed", None), ("completed", None), ("refused", "stop_open")]


def test_the_conductor_asked_for_the_held_out_set_exits_9_and_runs_nothing(workload_dir):
    done = subprocess.run(
        [*agentkit_command(), "conduct", "--workload-dir", str(workload_dir), "--set", "public_check", "--configurations", "concise,verbose"],
        capture_output=True, text=True, timeout=600,
    )
    assert done.returncode == 9, done.stdout + done.stderr
    assert human_lines() == []
    assert [(record["set"], record["status"], record["pairs"]) for record in read_records()] == [("public_check", "refused", [])]
    assert markers() == []


def test_the_conductor_refuses_the_live_adapter_with_exit_2_and_records_nothing(workload_dir):
    done = subprocess.run(
        [*agentkit_command(), "conduct", "--workload-dir", str(workload_dir), "--set", "dev", "--configurations", "concise", "--adapter", "live"],
        capture_output=True, text=True, timeout=600,
    )
    assert done.returncode == 2, done.stdout + done.stderr
    assert read_records() == []
    assert not CONDUCTOR_DIR.exists()


def test_a_child_of_the_conductor_that_sets_agentkit_process_to_cli_cannot_write_human_while_the_loop_runs(workload_dir, monkeypatch):
    hypotrail_command()
    children = []
    replay = ScriptedAdapter.answer

    def answer_and_start_a_child(self, case, repeat):
        if not children:
            children.append(subprocess.run(
                [*agentkit_command(), "decide", "--mark", "human", "--words-file", str(words()), "--question", "a7: resume?", "--option", "resume", "--run", "r-0001"],
                capture_output=True, text=True, env={**os.environ, decisions.PROCESS_VARIABLE: "cli"}, timeout=600,
            ))
        return replay(self, case, repeat)

    monkeypatch.setattr(ScriptedAdapter, "answer", answer_and_start_a_child)
    assert main(["conduct", "--workload-dir", str(workload_dir), "--set", "dev", "--configurations", "concise,verbose"]) == 0
    [child] = children
    assert child.returncode == 2, child.stdout + child.stderr
    assert human_lines() == []
    # The loop is over and its marker gone: an unmarked process writes HUMAN again.
    after = subprocess.run(
        [*agentkit_command(), "decide", "--mark", "human", "--words-file", str(words()), "--question", "a7: resume?", "--option", "resume", "--run", "r-0001"],
        capture_output=True, text=True, env={key: value for key, value in os.environ.items() if key != decisions.PROCESS_VARIABLE}, timeout=600,
    )
    assert after.returncode == 0, after.stdout + after.stderr


def test_the_marker_is_removed_when_the_loop_ends_on_an_error(workload_dir, monkeypatch):
    seen = []

    def failing_hypotrail(*args):
        seen.append(markers())
        return [sys.executable, "-c", "import sys; sys.exit(1)"]

    monkeypatch.setattr(conductor, "hypotrail_command", failing_hypotrail)
    assert main(["conduct", "--workload-dir", str(workload_dir), "--set", "dev", "--configurations", "concise,verbose"]) == 1
    assert seen and seen[0] == [CONDUCTOR_DIR / "c-0001.json"]
    assert markers() == []
    assert "hypotrail" in note()
