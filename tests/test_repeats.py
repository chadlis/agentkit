"""Repeats, and the `failed` and `missing` lines: every (Case, Repeat) of a run has its line, and a measure the adapter did not give is `null`. Offline; no model call."""

import json
from collections import Counter
from pathlib import Path

import jsonschema

from agentkit.adapters import RunStopped
from agentkit.adapters.scripted import Answer
from agentkit.cli import main
from agentkit.harness import run
from agentkit.ledger import read_records

ROOT = Path(__file__).resolve().parents[1]
LINE_SCHEMA = jsonschema.Draft202012Validator(json.loads((ROOT / "contracts" / "agent-answers-1" / "line.schema.json").read_text(encoding="utf-8")))


def read_export(out: Path) -> tuple[list[dict], dict]:
    lines = [json.loads(text) for text in (out / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    return lines, json.loads((out / "population.json").read_text(encoding="utf-8"))


def write_workload(directory: Path, fixture_rows: list[dict] | None = None) -> Path:
    """A Workload of 5 dev Cases, C1 to C5, and, when given, the fixture `answers-test.jsonl`, written by the test."""
    directory.mkdir()
    (directory / "workload.json").write_text(json.dumps({"cases": {"dev": "cases-dev.jsonl"}}), encoding="utf-8")
    rows = [{"case_id": f"C{number}", "question": f"Question {number}?", "group": "g", "family": "f"} for number in range(1, 6)]
    (directory / "cases-dev.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    if fixture_rows is not None:
        (directory / "fixtures").mkdir()
        (directory / "fixtures" / "answers-test.jsonl").write_text("".join(json.dumps(row) + "\n" for row in fixture_rows), encoding="utf-8")
    return directory


def test_three_repeats_on_dev_export_90_lines_and_a_population_of_the_same_90_pairs(workload_dir, tmp_path):
    out = tmp_path / "out"
    code = main(["export", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", "concise", "--repeats", "3", "--out", str(out)])
    assert code == 0
    lines, population = read_export(out)
    case_ids = list(dict.fromkeys(line["case_id"] for line in lines))
    expected = [{"case_id": case_id, "repeat": repeat} for case_id in case_ids for repeat in range(3)]
    assert len(case_ids) == 30
    assert [{"case_id": line["case_id"], "repeat": line["repeat"]} for line in lines] == expected
    assert population == {"episodes": expected}
    assert len(lines) == 90
    assert [[error.message for error in LINE_SCHEMA.iter_errors(line)] for line in lines] == [[] for _ in lines]


def test_repeats_below_1_are_invalid_input_and_record_no_run(workload_dir, tmp_path):
    code = main(["run", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", "concise", "--repeats", "0"])
    assert (code, read_records()) == (2, [])


class FailsOnceThenStops:
    """Answers every pair, except a `failed` answer for one pair and a stop of the run at a later one."""

    name = "fails-once-then-stops"
    settings = {"configuration": "fails-once-then-stops"}

    def __init__(self, fails_on: tuple[str, int], stops_on: tuple[str, int]):
        self.fails_on, self.stops_on = fails_on, stops_on

    def answer(self, case, repeat):
        if (case.case_id, repeat) == self.stops_on:
            raise RunStopped("test_stop", f"the test stops the run at {case.case_id}")
        if (case.case_id, repeat) == self.fails_on:
            return Answer(fields={"observation_status": "failed", "metadata": {"reason": "adapter_error", "error": "the test adapter failed"}})
        return Answer(fields={"observation_status": "produced", "raw_answer": f"An answer to {case.case_id}, repeat {repeat}."})


def test_an_adapter_that_fails_on_one_case_and_a_run_stopped_before_the_last_case_export_a_line_for_every_pair(tmp_path):
    workload = write_workload(tmp_path / "five")
    run_id = run(workload, "dev", FailsOnceThenStops(fails_on=("C2", 0), stops_on=("C4", 1)), repeats=2)
    out = tmp_path / "out"
    assert main(["export", "--run", run_id, "--out", str(out)]) == 0
    lines, population = read_export(out)
    pairs = [{"case_id": f"C{number}", "repeat": repeat} for number in range(1, 6) for repeat in range(2)]
    assert population == {"episodes": pairs}
    assert [{"case_id": line["case_id"], "repeat": line["repeat"]} for line in lines] == pairs
    statuses = Counter(line["observation_status"] for line in lines)
    assert statuses["failed"] == 1 and statuses["missing"] >= 1
    assert statuses == {"produced": 6, "failed": 1, "missing": 3}
    [failed] = [line for line in lines if line["observation_status"] == "failed"]
    assert (failed["case_id"], failed["repeat"], failed["metadata"]["reason"]) == ("C2", 0, "adapter_error")
    assert [line["metadata"] for line in lines if line["observation_status"] == "missing"] == [{"reason": "test_stop"}] * 3


def test_a_usage_measure_the_adapter_did_not_give_is_exported_null_not_0(tmp_path):
    fixture = [
        {"case_id": f"C{number}", "repeat": 0, "observation_status": "produced", "raw_answer": f"Answer {number}.", "usage": {"writer": {"tokens": 7}}}
        for number in range(1, 6)
    ]
    workload = write_workload(tmp_path / "five", fixture)
    out = tmp_path / "out"
    assert main(["export", "--workload-dir", str(workload), "--set", "dev", "--adapter", "scripted", "--fixture", "test", "--out", str(out)]) == 0
    lines, _ = read_export(out)
    assert [line["usage"] for line in lines] == [{"writer": {"tokens": 7, "latency_seconds": None, "cost_usd": None}}] * 5
    assert [[error.message for error in LINE_SCHEMA.iter_errors(line)] for line in lines] == [[] for _ in lines]


def test_exporting_an_existing_run_with_repeats_is_invalid_input(workload_dir, tmp_path):
    assert main(["run", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", "concise"]) == 0
    [record] = read_records()
    out = tmp_path / "out"
    assert (main(["export", "--run", record["run_id"], "--repeats", "3", "--out", str(out)]), out.exists()) == (2, False)


def test_every_failed_and_missing_line_of_a_live_provider_error_and_of_scripted_repeats_carries_metadata_reason(workload_dir, tmp_path, monkeypatch):
    from agentkit.adapters import live
    from test_budget import write_budget
    from test_live import MODEL, PRICE, FailingOnSecondCall, FakeClient, live_run

    monkeypatch.setattr(live, "AnthropicClient", FailingOnSecondCall, raising=False)
    monkeypatch.setenv("AGENTKIT_ANTHROPIC_API_KEY", "not-a-real-key")
    write_budget(live={"calls": 5, "tokens": 100000, "usd": 10}, prices={MODEL: PRICE})
    FakeClient.calls = []
    assert live_run(write_workload(tmp_path / "five"), "--live") == 1
    [record] = read_records()
    assert main(["export", "--run", record["run_id"], "--out", str(tmp_path / "live")]) == 0
    assert main(["export", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", "concise", "--repeats", "3", "--out", str(tmp_path / "scripted")]) == 0
    exported = {name: read_export(tmp_path / name)[0] for name in ("live", "scripted")}
    unanswered = {name: [line for line in lines if line["observation_status"] != "produced"] for name, lines in exported.items()}
    assert Counter(line["observation_status"] for line in unanswered["live"]) == {"failed": 1, "missing": 3}
    assert Counter(line["observation_status"] for line in unanswered["scripted"]) == {"missing": 60}
    reasons = {name: [isinstance(line.get("metadata", {}).get("reason"), str) for line in lines] for name, lines in unanswered.items()}
    assert reasons == {name: [True] * len(lines) for name, lines in unanswered.items()}


class FailsWithoutReason(FailsOnceThenStops):
    """Answers every pair, except a `failed` answer with no `metadata.reason` for one pair."""

    def answer(self, case, repeat):
        if (case.case_id, repeat) == self.fails_on:
            return Answer(fields={"observation_status": "failed", "metadata": {"error": "the test adapter failed"}})
        return super().answer(case, repeat)


def test_a_failed_answer_without_metadata_reason_is_refused_and_its_run_is_not_exported(tmp_path):
    fixture = [{"case_id": "C1", "repeat": 0, "observation_status": "failed", "metadata": {"error": "no reason given"}}]
    workload = write_workload(tmp_path / "five", fixture)
    assert main(["run", "--workload-dir", str(workload), "--set", "dev", "--adapter", "scripted", "--fixture", "test"]) == 2
    assert read_records() == []
    try:
        run(workload, "dev", FailsWithoutReason(fails_on=("C2", 0), stops_on=("none", 0)))
    except Exception as error:
        refusal = type(error).__name__
    else:
        refusal = None
    [record] = read_records()
    assert (refusal, record["status"], record.get("reason")) == ("WorkloadError", "failed", None)
    out = tmp_path / "out"
    assert (main(["export", "--run", record["run_id"], "--out", str(out)]), out.exists()) == (2, False)
