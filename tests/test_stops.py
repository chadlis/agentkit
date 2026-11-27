"""The stops (`docs/design.md`, section 6): a stop rule of `stops.json` that fires on a run exits 3 and writes `stops/STOP-<run>.json`, and an open stop blocks every run with exit 5 until a `HUMAN` decision cites it. Offline; no model call.

The words file here is written for the test and is no person's words."""

import json
from pathlib import Path

from agentkit import evaluator
from agentkit.cli import main
from agentkit.harness import run
from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.ledger import read_records
from test_budget import write_budget
from test_held_out import count_adapter_calls, evaluator_job

ROOT = Path(__file__).resolve().parents[1]


def workload(directory: Path, fixtures: dict[str, list[str]], partition: str = "dev") -> Path:
    """A Workload of 5 Cases, C1 to C5, of `partition`, and one fixture per name: the observation status of each Case in turn, `None` for a Case the fixture holds no answer for."""
    directory.mkdir()
    (directory / "workload.json").write_text(json.dumps({"cases": {partition: "cases.jsonl"}}), encoding="utf-8")
    rows = [{"case_id": f"C{number}", "question": f"Question {number}?", "group": "g", "family": "f"} for number in range(1, 6)]
    (directory / "cases.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (directory / "fixtures").mkdir()
    for name, statuses in fixtures.items():
        lines = []
        for number, status in enumerate(statuses, start=1):
            if status == "produced":
                lines.append({"case_id": f"C{number}", "repeat": 0, "observation_status": "produced", "raw_answer": f"Answer {number}."})
            elif status == "failed":
                lines.append({"case_id": f"C{number}", "repeat": 0, "observation_status": "failed", "metadata": {"reason": "adapter_error"}})
        (directory / "fixtures" / f"answers-{name}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return directory


FIXTURES = {
    "two-failed": ["produced", "failed", "produced", "failed", "produced"],
    "one-failed": ["produced", "produced", "failed", "produced", "produced"],
    "one-failed-one-missing": ["produced", "produced", "failed", "produced", None],
    "all-produced": ["produced"] * 5,
}


def dev_run(directory: Path, fixture: str) -> int:
    return main(["run", "--workload-dir", str(directory), "--set", "dev", "--adapter", "scripted", "--fixture", fixture])


def decide_on(stop: str, *mark: str) -> int:
    return main(["decide", "--question", f"Resume after stop {stop}?", "--option", "resume", "--stop", stop, *mark])


def words() -> Path:
    path = Path("words.txt")
    path.write_text("Example words written for this test, not a person's: resume.\n", encoding="utf-8")
    return path


def stop_file(run_id: str) -> dict:
    return json.loads((Path("stops") / f"STOP-{run_id}.json").read_text(encoding="utf-8"))


def test_a_fired_stop_exits_3_with_its_file_the_next_run_exits_5_a_proxy_decision_does_not_resume_it_and_a_human_one_does(tmp_path, monkeypatch):
    directory = workload(tmp_path / "five", FIXTURES)
    assert dev_run(directory, "two-failed") == 3
    stop = stop_file("r-0001")
    assert (stop["id"], stop["rule"], stop["run"]) == ("STOP-r-0001", "failed-share@1", "r-0001")
    assert read_records()[-1]["status"] == "completed"

    calls = count_adapter_calls(monkeypatch)
    assert dev_run(directory, "all-produced") == 5
    assert (read_records()[-1]["status"], read_records()[-1]["reason"], len(calls)) == ("refused", "stop_open", 0)

    assert decide_on("STOP-r-0001", "--mark", "proxy", "--rule", "resume-after-stop@1") == 0
    assert dev_run(directory, "all-produced") == 5

    assert decide_on("STOP-r-0001", "--mark", "human", "--words-file", str(words())) == 0
    assert dev_run(directory, "all-produced") == 0
    assert read_records()[-1]["status"] == "completed"


def test_a_human_decision_citing_another_stop_leaves_this_one_open(tmp_path):
    directory = workload(tmp_path / "five", FIXTURES)
    assert dev_run(directory, "two-failed") == 3
    Path("stops", "STOP-r-0099.json").write_text(json.dumps({"id": "STOP-r-0099", "rule": "failed-share@1", "run": "r-0099"}), encoding="utf-8")
    assert decide_on("STOP-r-0099", "--mark", "human", "--words-file", str(words())) == 0
    assert dev_run(directory, "all-produced") == 5


def test_failed_share_fires_above_one_fifth_of_the_produced_and_failed_lines_and_missing_lines_do_not_count(tmp_path):
    directory = workload(tmp_path / "five", FIXTURES)
    # 1 failed of 5: a share of 0.2, not above it.
    assert dev_run(directory, "one-failed") == 0
    # 1 failed of 4 produced and failed, the fifth missing: 0.25. Counted with the missing line it would be 0.2.
    assert dev_run(directory, "one-failed-one-missing") == 3
    assert stop_file("r-0002")["rule"] == "failed-share@1"


def test_the_dev_run_that_spends_the_last_of_the_dev_runs_fires_budget_reached(tmp_path):
    directory = workload(tmp_path / "five", FIXTURES)
    write_budget(dev_runs=2)
    assert dev_run(directory, "all-produced") == 0
    assert dev_run(directory, "all-produced") == 3
    assert stop_file("r-0002")["rule"] == "budget-reached@1"
    assert dev_run(directory, "all-produced") == 5
    assert decide_on("STOP-r-0002", "--mark", "human", "--words-file", str(words())) == 0
    assert dev_run(directory, "all-produced") == 8


def test_the_stop_rules_apply_to_dev_only(tmp_path, monkeypatch):
    directory = workload(tmp_path / "five", FIXTURES, partition=evaluator.HELD_OUT)
    write_budget(held_out_readings=1)
    evaluator_job(monkeypatch)
    with evaluator.entry_point():
        run_id = run(directory, evaluator.HELD_OUT, ScriptedAdapter(directory / "fixtures" / "answers-two-failed.jsonl"))
    [record] = read_records()
    assert (record["run_id"], record["status"], record.get("stop")) == (run_id, "completed", None)
    assert not Path("stops").exists()


def test_a_stopped_live_run_keeps_its_exit_and_fires_no_stop(tmp_path, monkeypatch):
    from agentkit.adapters import live
    from test_live import MODEL, PRICE, FailingOnSecondCall, FakeClient, live_run

    directory = workload(tmp_path / "five", {})
    monkeypatch.setattr(live, "AnthropicClient", FailingOnSecondCall, raising=False)
    monkeypatch.setenv("AGENTKIT_ANTHROPIC_API_KEY", "not-a-real-key")
    # The second of two dev runs spends the last of the dev runs.
    write_budget(dev_runs=2, live={"calls": 5, "tokens": 100000, "usd": 10}, prices={MODEL: PRICE})
    FakeClient.calls = []
    # One produced line and one failed, a share of 0.5: the run stopped on a provider error.
    assert live_run(directory, "--live") == 1
    # The fake client fails only on its second call ever: here both calls answer, and the third stops at the call ceiling.
    write_budget(dev_runs=2, live={"calls": 2, "tokens": 100000, "usd": 10}, prices={MODEL: PRICE})
    assert live_run(directory, "--live") == 8
    assert [(record["status"], record["reason"]) for record in read_records()] == [("failed", "provider_error"), ("failed", "call_ceiling")]
    assert not Path("stops").exists()


def test_a_malformed_stops_file_is_invalid_input_and_records_nothing(tmp_path):
    directory = workload(tmp_path / "five", FIXTURES)
    Path("stops.json").write_text(json.dumps({"reason": "r", "rules": {"no-such-rule@1": {}}}), encoding="utf-8")
    assert dev_run(directory, "all-produced") == 2
    Path("stops.json").unlink()
    assert dev_run(directory, "all-produced") == 2
    assert read_records() == []


def test_the_versioned_stops_file_holds_the_two_rules_of_the_proxy_decision():
    stops = json.loads((ROOT / "stops.json").read_text(encoding="utf-8"))
    assert stops["rules"] == {"budget-reached@1": {}, "failed-share@1": {"above": 0.2}}
