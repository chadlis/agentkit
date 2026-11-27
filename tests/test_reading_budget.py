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
from conftest import reservation_history
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


def reservation(name, run, *, configuration=None):
    event = record(name, run, configuration=configuration)
    del event["status"]
    return {"kind": "reserve", "hypotrail_commit": COMMIT, **event}


@pytest.fixture
def synthetic_job(monkeypatch, tmp_path):
    mark_evaluator(monkeypatch)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    directory = tmp_path / "budget-checkout"
    append = reservation_history(directory)
    monkeypatch.setattr(evaluator_job, "job", lambda: evaluator_job.Job("example/kit", COMMIT, RUN, "example"))
    monkeypatch.setattr(evaluator_job, "checkout_commit", lambda directory: COMMIT)
    monkeypatch.setattr(evaluator_job.reservations, "verify_frozen_fixture", lambda *args: None)
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

    def read(name, out="published"):
        return main([
            "evaluate", "--workload-dir", str(tmp_path), "--candidate", name, "--out", out,
            "--reservations-checkout", str(directory),
        ])

    return read, seen, append


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_a_committed_reading_refuses_the_same_candidate_from_an_empty_ledger(synthetic_job, status):
    read, seen, append = synthetic_job
    append(reservation("first", 1))
    Path("earlier-outcome.jsonl").write_text(json.dumps(record("first", 1, status=status)) + "\n", encoding="utf-8")
    assert read("first") == 8
    assert seen == []
    assert not Path("published").exists()
    assert [row["reason"] for row in ledger.read_records()] == ["held_out_readings"]


def test_the_same_name_cannot_be_read_again_with_changed_settings_in_the_same_directory(synthetic_job, monkeypatch):
    read, seen, append = synthetic_job
    append(reservation("first", 100))
    assert read("first") == 0
    monkeypatch.setattr(
        evaluator_job, "ScriptedAdapter",
        lambda fixture: SimpleNamespace(name="scripted", settings=settings("changed")),
    )
    assert read("first", "again") == 8
    assert seen == ["entered"]


def test_another_candidate_is_admitted_up_to_the_total_budget(synthetic_job, monkeypatch):
    read, seen, append = synthetic_job
    append(reservation("first", 1))
    append(reservation("second", 2))
    append(reservation("third", 100))
    assert read("third") == 0
    assert seen == ["entered"]
    assert json.loads(Path("published/reading.jsonl").read_text()) == record("third", 100)
    another = "https://github.com/example/kit/actions/runs/101"
    monkeypatch.setattr(evaluator_job, "job", lambda: evaluator_job.Job("example/kit", COMMIT, another, "example"))
    assert read("fourth", "refused") == 8
    assert seen == ["entered"]
    assert not Path("refused").exists()
    assert ledger.read_records()[-1]["reason"] == "held_out_total_readings"


def test_three_committed_readings_refuse_a_fourth_from_an_empty_ledger(synthetic_job):
    read, seen, append = synthetic_job
    append(reservation("first", 1))
    append(reservation("second", 2))
    append(reservation("third", 3))
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
    read, seen, append = synthetic_job
    for run, name in enumerate(names, start=1):
        append(reservation(name, run))
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
    read, seen, append = synthetic_job
    append(reservation("old-name", 1, configuration=settings("new-name")))
    append(reservation("new-name", 100))
    assert read("new-name") == 8
    assert seen == []


def test_a_merged_reading_with_a_local_ledger_record_spends_only_once(synthetic_job, monkeypatch):
    read, seen, append = synthetic_job
    append(reservation("first", 100))
    assert read("first") == 0
    append(reservation("second", 2))
    append(reservation("third", 101))
    another = "https://github.com/example/kit/actions/runs/101"
    monkeypatch.setattr(evaluator_job, "job", lambda: evaluator_job.Job("example/kit", COMMIT, another, "example"))
    assert read("third", "next-output") == 0
    assert seen == ["entered", "entered"]


def test_a_counted_failure_leaves_only_its_public_metadata(synthetic_job, monkeypatch, capsys):
    read, _, append = synthetic_job

    def fail_after_recording(*args):
        assert json.loads(Path("published/reading.jsonl").read_text()) == record("first", 100, status="failed")
        raise RuntimeError("synthetic detail never published")

    monkeypatch.setattr(evaluator_job, "_inside", fail_after_recording)
    append(reservation("first", 100))
    assert read("first") == 1
    assert [path.name for path in Path("published").iterdir()] == ["reading.jsonl"]
    assert json.loads(Path("published/reading.jsonl").read_text()) == record("first", 100, status="failed")
    assert "synthetic detail never published" not in capsys.readouterr().err
    assert [(row["status"], row["reason"], row["pairs"]) for row in ledger.read_records()] == [("failed", "error", [])]


def test_a_missing_committed_reservation_refuses_before_the_fixture_is_opened(synthetic_job, monkeypatch):
    read, seen, _ = synthetic_job
    monkeypatch.setattr(evaluator_job, "ScriptedAdapter", lambda *args: pytest.fail("opened an unreserved fixture"))
    assert read("first") == 8
    assert seen == []
    assert [row["reason"] for row in ledger.read_records()] == ["reading_reservation"]
    assert not Path("published").exists()


def test_a_configuration_mismatch_uses_no_adapter_calls(synthetic_job):
    read, seen, append = synthetic_job
    append(reservation("first", 100, configuration=settings("different")))
    assert read("first") == 8
    assert seen == []
    assert [(row["reason"], row["configuration"]) for row in ledger.read_records()] == [("reading_reservation", None)]
    assert not Path("published").exists()


def test_interruption_after_preflight_keeps_the_reservation_with_no_local_reading(synthetic_job, monkeypatch):
    read, seen, append = synthetic_job
    append(reservation("first", 100))
    append(reservation("second", 2))
    append(reservation("third", 3))
    assert main([
        "verify-reservation", "--candidate", "first", "--hypotrail-commit", COMMIT,
        "--reservations-checkout", "budget-checkout",
    ]) == 0
    assert ledger.read_records() == seen == []
    another = "https://github.com/example/kit/actions/runs/101"
    monkeypatch.setattr(evaluator_job, "job", lambda: evaluator_job.Job("example/kit", COMMIT, another, "example"))
    assert read("fourth") == 8
    assert [row["reason"] for row in ledger.read_records()] == ["held_out_total_readings"]
    assert seen == []


def test_a_protected_read_requires_an_explicit_total_in_the_budget(synthetic_job, monkeypatch, capsys):
    read, seen, append = synthetic_job
    append(reservation("first", 100))
    path = Path("budget.json")
    limits = json.loads(path.read_text(encoding="utf-8"))
    del limits["held_out_total_readings"]
    path.write_text(json.dumps(limits), encoding="utf-8")
    monkeypatch.setattr(evaluator_job, "ScriptedAdapter", lambda *args: pytest.fail("opened a fixture without a total limit"))
    assert read("first") == 2
    assert "protected readings require held_out_total_readings" in capsys.readouterr().err
    assert seen == ledger.read_records() == []
    assert not Path("published").exists()


def test_a_malformed_record_is_refused_without_spending(synthetic_job):
    read, seen, append = synthetic_job
    append({**reservation("first", 1), "unexpected": "synthetic"})
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
