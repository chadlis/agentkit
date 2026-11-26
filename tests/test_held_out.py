"""The held-out refusal (`docs/design.md`, section 3): the `public_check` Partition is read only under the evaluator entry point, which checks that it runs in the evaluator workflow of a CI job. A guard in code, not a wall. Offline; no model call.

These tests count the held-out Cases and never print them."""

import pytest

from agentkit import evaluator
from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cases import read_cases
from agentkit.cli import main
from agentkit.evaluator import HeldOutRefused
from agentkit.ledger import answers_file, LEDGER_DIR, read_records

EVALUATOR_WORKFLOW_REF = "chadlis/agentkit/.github/workflows/evaluator.yml@refs/heads/main"


def plain_process(monkeypatch) -> None:
    """No variable of a GitHub Actions job: a local process."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_WORKFLOW_REF", raising=False)


def evaluator_job(monkeypatch, workflow_ref: str = EVALUATOR_WORKFLOW_REF) -> None:
    """The variables GitHub Actions sets in a job of the evaluator workflow, imitated."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", workflow_ref)


def count_adapter_calls(monkeypatch) -> list:
    calls = []
    replay = ScriptedAdapter.answer

    def counted(self, case, repeat):
        calls.append((case.case_id, repeat))
        return replay(self, case, repeat)

    monkeypatch.setattr(ScriptedAdapter, "answer", counted)
    return calls


def run_held_out(workload_dir) -> int:
    return main(["run", "--workload-dir", str(workload_dir), "--set", "public_check", "--adapter", "scripted", "--fixture", "concise"])


def test_a_held_out_run_from_a_plain_process_exits_9_with_a_refused_record_and_no_adapter_call(workload_dir, monkeypatch):
    plain_process(monkeypatch)
    calls = count_adapter_calls(monkeypatch)
    assert run_held_out(workload_dir) == 9
    records = read_records()
    assert [(record["set"], record["adapter"], record["status"], record["pairs"]) for record in records] == [("public_check", "scripted", "refused", [])]
    [record] = records
    assert record["start_time"] and record["end_time"]
    assert calls == []
    assert not answers_file(LEDGER_DIR, record["run_id"]).exists()


def test_reading_the_held_out_cases_outside_the_entry_point_is_refused_even_in_the_evaluator_job(workload_dir, monkeypatch):
    evaluator_job(monkeypatch)
    with pytest.raises(HeldOutRefused):
        read_cases(workload_dir, "public_check")


def test_the_entry_point_in_the_evaluator_job_reads_the_6_held_out_cases(workload_dir, monkeypatch):
    evaluator_job(monkeypatch)
    with evaluator.entry_point():
        cases = read_cases(workload_dir, "public_check")
    dev_ids = {case.case_id for case in read_cases(workload_dir, "dev")}
    held_out_ids = {case.case_id for case in cases}
    assert len(cases) == len(held_out_ids) == 6
    assert held_out_ids.isdisjoint(dev_ids)


@pytest.mark.parametrize(
    "environment",
    [
        pytest.param({}, id="plain-process"),
        pytest.param({"GITHUB_WORKFLOW_REF": EVALUATOR_WORKFLOW_REF}, id="not-github-actions"),
        pytest.param({"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": "chadlis/agentkit/.github/workflows/ci.yml@refs/heads/main"}, id="another-workflow"),
        pytest.param({"GITHUB_ACTIONS": "true"}, id="no-workflow-ref"),
    ],
)
def test_the_entry_point_refuses_outside_a_job_of_the_evaluator_workflow(workload_dir, monkeypatch, environment):
    plain_process(monkeypatch)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(HeldOutRefused):
        with evaluator.entry_point():
            read_cases(workload_dir, "public_check")


def test_a_held_out_export_from_a_plain_process_exits_9_and_writes_nothing(workload_dir, monkeypatch, tmp_path):
    plain_process(monkeypatch)
    out = tmp_path / "out"
    assert main(["export", "--workload-dir", str(workload_dir), "--set", "public_check", "--adapter", "scripted", "--fixture", "concise", "--out", str(out)]) == 9
    assert [record["status"] for record in read_records()] == ["refused"]
    assert not out.exists()


def test_an_export_of_a_refused_run_is_refused_with_exit_2_and_writes_nothing(workload_dir, monkeypatch, tmp_path):
    plain_process(monkeypatch)
    assert run_held_out(workload_dir) == 9
    assert [record["status"] for record in read_records()] == ["refused"]
    [record] = read_records()
    out = tmp_path / "out"
    assert main(["export", "--run", record["run_id"], "--out", str(out)]) == 2
    assert not out.exists()
