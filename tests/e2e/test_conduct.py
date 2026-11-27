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
from test_dev_end_to_end import agentkit_command, hypotrail_command, kit_clone

CONDUCTOR_DIR = Path("ledger") / "conductor"
SITECUSTOMIZE = '''
import os, sys

_log = os.environ["AGENTKIT_AUDIT_LOG"]
_references = os.environ["AGENTKIT_AUDIT_WATCH"]


def _hook(event, args):
    if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)) or os.path.basename(sys.argv[0]) != "agentkit":
        return
    path = os.path.realpath(os.fsdecode(args[0]))
    parts = path.split(os.sep)
    # The conductor's Study lives in a temporary directory named agentkit-<conduct_id>-...
    inside = "study" in parts and any(part.startswith("agentkit-c-") for part in parts)
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


def held_out_needles(workload_dir: Path) -> list[str]:
    """Every held-out Case id and the reference text of each held-out Case, as the twin holds them."""
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    held_out = [json.loads(text)["case_id"] for text in (workload_dir / workload["cases"]["public_check"]).read_text(encoding="utf-8").splitlines() if text.strip()]
    references = [json.loads(text) for text in (workload_dir / workload["references"]).read_text(encoding="utf-8").splitlines() if text.strip()]
    return held_out + [reference["reference_text"] for reference in references if reference["case_id"] in held_out]


def files_holding(needles: list[str], files: list[Path]) -> int:
    """How many of `files` hold any of `needles`; a count only, so that a failure shows no held-out text."""
    return sum(any(needle in path.read_text(encoding="utf-8", errors="replace") for needle in needles) for path in files if path.is_file())


def test_after_a_conduct_no_file_it_writes_in_the_kit_or_under_ledger_holds_a_held_out_case_id_or_reference_text(workload_dir, tmp_path, monkeypatch):
    hypotrail_command()
    needles = held_out_needles(workload_dir)
    found = len(needles)
    assert found == 12
    # The conductor runs from the root of a clone of the kit, so that every file it writes there is one `git status` lists.
    work = kit_clone(tmp_path)
    monkeypatch.chdir(work)
    # The temporary directory the conductor's Study lives in, outside its working directory.
    temporary = tmp_path.parent / f"{tmp_path.name}-a7-tmp"
    temporary.mkdir()
    done = conduct(workload_dir, "concise,verbose", env={"TMPDIR": str(temporary)})
    assert done.returncode == 0, done.stdout + done.stderr
    listed = subprocess.run(["git", "status", "--porcelain", "--ignored", "--untracked-files=all"], capture_output=True, text=True, check=True).stdout.splitlines()
    written = [Path(line[3:]) for line in listed if not line[3:].startswith("a7-audit/")]
    assert any(path.parts[:2] == ("ledger", "conductor") for path in written)
    holding = files_holding(needles, written)
    assert holding == 0
    assert list(temporary.iterdir()) == []
    [comparison] = [json.loads(text) for text in (CONDUCTOR_DIR / "c-0001-comparisons.jsonl").read_text(encoding="utf-8").splitlines()]
    assert (comparison["control_run"], comparison["candidate_run"], comparison["delta"]["control"], comparison["delta"]["candidate"]) == ("r-0001", "r-0002", 1.0, 2 / 3)


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
            # A child started in another directory, citing a pull request in its form so that its source is valid there.
            elsewhere = Path("a7-elsewhere")
            elsewhere.mkdir()
            children.append(subprocess.run(
                [*agentkit_command(), "decide", "--mark", "human", "--words-file", str(words().resolve()), "--question", "a7: resume?", "--option", "resume",
                 "--pull-request", "https://github.com/chadlis/agentkit/pull/23@" + "0" * 40],
                capture_output=True, text=True, cwd=elsewhere, env={**os.environ, decisions.PROCESS_VARIABLE: "cli"}, timeout=600,
            ))
        return replay(self, case, repeat)

    monkeypatch.setattr(ScriptedAdapter, "answer", answer_and_start_a_child)
    assert main(["conduct", "--workload-dir", str(workload_dir), "--set", "dev", "--configurations", "concise,verbose"]) == 0
    [child, elsewhere] = children
    assert child.returncode == 2, child.stdout + child.stderr
    assert elsewhere.returncode == 2, elsewhere.stdout + elsewhere.stderr
    assert not (Path("a7-elsewhere") / "decisions" / "log.jsonl").exists()
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


def test_a_conductor_sent_sigterm_mid_loop_removes_its_temporary_study_and_its_marker(workload_dir, tmp_path):
    import signal
    import time

    hypotrail_command()
    temporary = tmp_path.parent / f"{tmp_path.name}-a7-tmp"
    temporary.mkdir()
    environment = {key: value for key, value in os.environ.items() if key != "HYPOTRAIL_ANTHROPIC_API_KEY"} | {"TMPDIR": str(temporary)}
    command = [*agentkit_command(), "conduct", "--workload-dir", str(workload_dir), "--set", "dev", "--configurations", "concise,verbose"]
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 120
    # Mid-loop: once `hypotrail init` has copied the References into the temporary Study.
    while not list(temporary.glob("agentkit-c-*/study/artifacts/*")) and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert process.poll() is None, "the loop ended before the temporary Study was made"
    assert markers() != []
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=120)
    assert (list(temporary.iterdir()), markers()) == ([], [])
    assert process.returncode == 128 + signal.SIGTERM
