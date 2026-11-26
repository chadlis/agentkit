"""The budget, `budget.json` of the working directory (`docs/design.md`, section 5): every limit is checked before spending, and a run that would exceed one exits 8 with a `refused` ledger record and starts nothing. Offline; no model call."""

import json
from pathlib import Path

from agentkit import evaluator
from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cli import main
from agentkit.harness import run
from agentkit.ledger import counts, read_records
from test_held_out import count_adapter_calls, evaluator_job

ROOT = Path(__file__).resolve().parents[1]


def write_budget(**limits) -> None:
    """The versioned `budget.json` in the working directory, with `limits` replacing its keys."""
    budget = json.loads((ROOT / "budget.json").read_text(encoding="utf-8")) | limits
    Path("budget.json").write_text(json.dumps(budget), encoding="utf-8")


def dev_run(workload_dir: Path) -> int:
    return main(["run", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", "concise"])


def test_a_dev_budget_of_2_runs_twice_then_refuses_the_third_with_exit_8_and_the_ledger_counts_2_completed_and_1_refused(workload_dir, monkeypatch):
    write_budget(dev_runs=2)
    calls = count_adapter_calls(monkeypatch)
    assert [dev_run(workload_dir) for _ in range(3)] == [0, 0, 8]
    assert len(calls) == 2 * 30
    assert counts(read_records())["dev"] == {"completed": 2, "failed": 0, "refused": 1, "running": 0}
    refused = read_records()[-1]
    assert (refused["status"], refused["reason"], refused["pairs"]) == ("refused", "dev_runs", [])


def test_a_second_held_out_reading_of_the_same_configuration_is_refused_with_a_refused_record(workload_dir, monkeypatch):
    write_budget(held_out_readings=1)
    evaluator_job(monkeypatch)
    fixture = workload_dir / "fixtures" / "answers-concise.jsonl"
    with evaluator.entry_point():
        run(workload_dir, "public_check", ScriptedAdapter(fixture))
        calls = count_adapter_calls(monkeypatch)
        try:
            run(workload_dir, "public_check", ScriptedAdapter(fixture))
        except Exception as error:
            refusal = type(error).__name__
        else:
            refusal = None
    assert (refusal, len(calls)) == ("BudgetRefused", 0)
    assert [(record["status"], record.get("reason")) for record in read_records()] == [("completed", None), ("refused", "held_out_readings")]


def test_a_run_without_a_budget_file_is_invalid_input_and_records_nothing(workload_dir):
    Path("budget.json").unlink()
    assert dev_run(workload_dir) == 2
    assert read_records() == []


def test_the_versioned_budget_turns_live_spending_off():
    budget = json.loads((ROOT / "budget.json").read_text(encoding="utf-8"))
    assert budget["live"] == {"calls": 0, "tokens": 0, "usd": 0}
    assert budget["prices"] == {}
