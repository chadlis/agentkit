"""The run ledger, `ledger/runs.jsonl`: every harness run is counted before it spends, and `agentkit status` reads it without writing. Offline; no model call."""

import json
from pathlib import Path

import pytest

from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cli import main
from agentkit.harness import run
from agentkit.ledger import LEDGER_DIR, read_records


def scripted(workload_dir: Path, fixture: str = "concise") -> ScriptedAdapter:
    return ScriptedAdapter(workload_dir / "fixtures" / f"answers-{fixture}.jsonl")


class CrashAfterFirstCase:
    """Answers the first Case as the concise fixture does, then raises."""

    name = "crash-after-first-case"

    def __init__(self, workload_dir: Path):
        self._inner = scripted(workload_dir)
        self.settings = self._inner.settings
        self.calls = 0

    def answer(self, case, repeat):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("the adapter crashed")
        return self._inner.answer(case, repeat)


class LedgerWitness:
    """Replays the concise fixture, and on its first call records what the ledger held at that moment."""

    name = "ledger-witness"

    def __init__(self, workload_dir: Path):
        self._inner = scripted(workload_dir)
        self.settings = self._inner.settings
        self.seen_at_first_call: list[dict] | None = None

    def answer(self, case, repeat):
        if self.seen_at_first_call is None:
            self.seen_at_first_call = read_records(LEDGER_DIR)
        return self._inner.answer(case, repeat)


def crashed_run(workload_dir: Path) -> str:
    with pytest.raises(RuntimeError, match="the adapter crashed"):
        run(workload_dir, "dev", CrashAfterFirstCase(workload_dir))
    [record] = read_records()
    return record["run_id"]


def tree(root: Path) -> dict[str, tuple]:
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else ("dir",) for path in root.rglob("*")}


def test_three_dev_runs_leave_three_completed_records_and_status_reports_three_dev_runs(workload_dir, tmp_path, capsys):
    for _ in range(3):
        assert main(["run", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", "concise"]) == 0
    records = read_records()
    assert [(record["set"], record["adapter"], record["status"]) for record in records] == [("dev", "scripted", "completed")] * 3
    assert len({record["run_id"] for record in records}) == 3
    assert all(len(record["pairs"]) == 30 and record["end_time"] for record in records)
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "dev: 3 runs (3 completed, 0 failed, 0 refused, 0 running)" in capsys.readouterr().out.splitlines()


def test_a_run_whose_adapter_crashes_stays_running_and_the_next_run_marks_it_failed_before_it_starts(workload_dir, tmp_path):
    crashed = crashed_run(workload_dir)
    [record] = read_records()
    assert (record["status"], record["end_time"]) == ("running", None)
    witness = LedgerWitness(workload_dir)
    run(workload_dir, "dev", witness)
    assert witness.seen_at_first_call is not None
    statuses = {item["run_id"]: item["status"] for item in witness.seen_at_first_call}
    assert statuses[crashed] == "failed"
    assert [item["status"] for item in read_records()] == ["failed", "completed"]


def test_the_running_record_with_its_pairs_exists_before_the_adapters_first_call(workload_dir, tmp_path):
    witness = LedgerWitness(workload_dir)
    run_id = run(workload_dir, "dev", witness)
    assert witness.seen_at_first_call is not None
    assert [item["run_id"] for item in witness.seen_at_first_call] == [run_id]
    [record] = witness.seen_at_first_call
    assert (record["run_id"], record["status"], record["set"], record["adapter"]) == (run_id, "running", "dev", "ledger-witness")
    assert record["configuration"] == witness.settings
    assert len(record["pairs"]) == 30 and record["start_time"] and record["end_time"] is None


def test_an_export_from_a_failed_run_is_refused_with_exit_2_and_writes_nothing(workload_dir, tmp_path):
    crashed = crashed_run(workload_dir)
    run(workload_dir, "dev", scripted(workload_dir))
    assert {item["run_id"]: item["status"] for item in read_records()}[crashed] == "failed"
    out = tmp_path / "out"
    assert main(["export", "--run", crashed, "--out", str(out)]) == 2
    assert not out.exists()


def test_an_export_from_a_completed_run_writes_the_answers_the_run_recorded(workload_dir, tmp_path):
    run_id = run(workload_dir, "dev", scripted(workload_dir))
    assert main(["export", "--run", run_id, "--out", str(tmp_path / "out")]) == 0
    lines = [json.loads(text) for text in (tmp_path / "out" / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    [record] = read_records()
    assert [[line["case_id"], line["repeat"]] for line in lines] == record["pairs"]
    assert {line["observation_status"] for line in lines} == {"produced"}
    assert all(line["settings"] == record["configuration"] and line["origin"] == "authored_fixture" for line in lines)


def test_status_writes_no_file(workload_dir, tmp_path, capsys):
    crashed_run(workload_dir)
    before = tree(tmp_path)
    assert main(["status"]) == 0
    assert "dev: 1 run (0 completed, 0 failed, 0 refused, 1 running)" in capsys.readouterr().out.splitlines()
    assert tree(tmp_path) == before
