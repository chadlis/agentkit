"""Committed reading limits with synthetic Configurations and no Case access."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agentkit import evaluator_job, ledger
from agentkit.cli import main
from test_held_out import evaluator_job as mark_evaluator

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
RUN = "https://github.com/example/kit/actions/runs/100"


def settings(name):
    return {"adapter": "scripted", "fixture": f"answers-{name}.jsonl", "revision": "synthetic"}


def record(name, run, *, status="completed", configuration=None):
    if configuration is None:
        configuration = settings(name)
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return {
        "candidate": name,
        "configuration_digest": hashlib.sha256(encoded).hexdigest(),
        "job_commit": COMMIT,
        "job_run": f"https://github.com/example/kit/actions/runs/{run}",
        "status": status,
    }


def commit_readings(*items):
    Path("readings.jsonl").write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")


@pytest.fixture
def synthetic_job(monkeypatch, tmp_path):
    mark_evaluator(monkeypatch)
    monkeypatch.setattr(evaluator_job, "job", lambda: evaluator_job.Job("example/kit", COMMIT, RUN, "example"))
    monkeypatch.setattr(evaluator_job, "checkout_commit", lambda directory: COMMIT)
    monkeypatch.setattr(
        evaluator_job,
        "ScriptedAdapter",
        lambda fixture: SimpleNamespace(name="scripted", settings=settings(fixture.stem.removeprefix("answers-"))),
    )
    seen = []

    def inside(*args):
        seen.append("entered")
        return Counter(produced=1), {"aggregates": [], "observation_set_ids": [], "assessment_ids": []}

    monkeypatch.setattr(evaluator_job, "_inside", inside)
    commit_readings()

    def read(name, out="published"):
        return main(["evaluate", "--workload-dir", str(tmp_path), "--candidate", name, "--out", out])

    return read, seen


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_a_committed_reading_refuses_the_same_candidate_from_an_empty_ledger(synthetic_job, status):
    read, seen = synthetic_job
    commit_readings(record("first", 1, status=status))
    assert read("first") == 8
    assert seen == []
    assert not Path("published").exists()
    assert [row["reason"] for row in ledger.read_records()] == ["held_out_readings"]


def test_the_same_name_cannot_be_read_again_with_changed_settings_in_the_same_directory(synthetic_job, monkeypatch):
    read, seen = synthetic_job
    assert read("first") == 0
    monkeypatch.setattr(
        evaluator_job, "ScriptedAdapter",
        lambda fixture: SimpleNamespace(name="scripted", settings=settings("changed")),
    )
    assert read("first", "again") == 8
    assert seen == ["entered"]


def test_another_candidate_is_admitted_up_to_the_total_budget(synthetic_job):
    read, seen = synthetic_job
    commit_readings(record("first", 1), record("second", 2, status="failed"))
    assert read("third") == 0
    assert seen == ["entered"]
    assert json.loads(Path("published/reading.jsonl").read_text()) == record("third", 100)
    assert read("fourth", "refused") == 8
    assert seen == ["entered"]
    assert not Path("refused").exists()
    assert ledger.read_records()[-1]["reason"] == "held_out_total_readings"


def test_three_committed_readings_refuse_a_fourth_from_an_empty_ledger(synthetic_job):
    read, seen = synthetic_job
    commit_readings(record("first", 1), record("second", 2), record("third", 3, status="failed"))
    assert read("fourth") == 8
    assert seen == []
    assert not Path("published").exists()
    assert [row["reason"] for row in ledger.read_records()] == ["held_out_total_readings"]


@pytest.mark.parametrize(
    ("names", "candidate", "reason"),
    [
        (("first",), "first", "held_out_readings"),
        (("first", "second", "third"), "fourth", "held_out_total_readings"),
    ],
)
def test_a_known_budget_refusal_happens_before_a_malformed_fixture_is_read(synthetic_job, monkeypatch, names, candidate, reason):
    read, seen = synthetic_job
    commit_readings(*(record(name, run) for run, name in enumerate(names, start=1)))
    fixture_reads = []

    def malformed(fixture):
        fixture_reads.append(fixture)
        raise evaluator_job.FixtureError("malformed synthetic fixture", "malformed synthetic fixture")

    monkeypatch.setattr(evaluator_job, "ScriptedAdapter", malformed)
    assert read(candidate) == 8
    assert fixture_reads == seen == []
    assert [row["reason"] for row in ledger.read_records()] == [reason]
    assert not Path("published").exists()


def test_a_renamed_configuration_cannot_be_read_again(synthetic_job):
    read, seen = synthetic_job
    commit_readings(record("old-name", 1, configuration=settings("new-name")))
    assert read("new-name") == 8
    assert seen == []


def test_a_merged_reading_with_a_local_ledger_record_spends_only_once(synthetic_job, monkeypatch):
    read, seen = synthetic_job
    assert read("first") == 0
    commit_readings(record("first", 100), record("second", 2))
    another = "https://github.com/example/kit/actions/runs/101"
    monkeypatch.setattr(evaluator_job, "job", lambda: evaluator_job.Job("example/kit", COMMIT, another, "example"))
    assert read("third", "next-output") == 0
    assert seen == ["entered", "entered"]


def test_a_counted_failure_leaves_only_its_public_metadata(synthetic_job, monkeypatch, capsys):
    read, _ = synthetic_job

    def fail_after_recording(*args):
        assert json.loads(Path("published/reading.jsonl").read_text()) == record("first", 100, status="failed")
        raise RuntimeError("synthetic detail never published")

    monkeypatch.setattr(evaluator_job, "_inside", fail_after_recording)
    assert read("first") == 1
    assert [path.name for path in Path("published").iterdir()] == ["reading.jsonl"]
    assert json.loads(Path("published/reading.jsonl").read_text()) == record("first", 100, status="failed")
    assert "synthetic detail never published" not in capsys.readouterr().err
    assert [(row["status"], row["reason"], row["pairs"]) for row in ledger.read_records()] == [("failed", "error", [])]


def test_a_malformed_record_is_refused_without_spending(synthetic_job):
    read, seen = synthetic_job
    commit_readings({**record("first", 1), "unexpected": "synthetic"})
    assert read("another") == 2
    assert seen == []
    assert ledger.read_records() == []
    assert not Path("published").exists()


def test_the_committed_budget_is_three_and_the_evaluator_serialises_jobs():
    limits = json.loads((ROOT / "budget.json").read_text(encoding="utf-8"))
    assert limits["held_out_readings"] == 1
    assert limits.get("held_out_total_readings") == 3
    workflow = yaml.safe_load((ROOT / ".github/workflows/evaluator.yml").read_text(encoding="utf-8"))
    assert workflow["concurrency"] == {"group": "held-out-evaluator", "cancel-in-progress": False}
    assert workflow["permissions"] == {"contents": "read"}
