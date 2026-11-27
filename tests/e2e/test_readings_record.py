"""The readings record across job runs, chadlis/agentkit#25: every job of `evaluator.yml` starts with an empty ledger, so before a reading it checks `readings.jsonl` at its commit, the record a person extends through a pull request with the line each reading publishes.

The variables of an evaluator job are imitated, as in `test_evaluate.py`. Offline; no model call. A failing assertion here shows counts, ids and digests only, never a held-out question, answer or Reference text."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentkit import conductor, readings
from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cli import main
from agentkit.ledger import read_records
from test_dev_end_to_end import ROOT, hypotrail_command
from test_evaluate import JOB_SHA, environment, evaluate_command, job, published

# The commit dates the tests give: the Workload copy, then the first reading's job commit.
WORKLOAD_MADE = "2026-01-01T00:00:00+00:00"
FIRST_READING = "2026-06-01T00:00:00+00:00"
BEFORE_IT, AFTER_IT = "2026-03-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"


def git(cwd: Path, *args: str, date: str | None = None) -> str:
    dated = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else {}
    done = subprocess.run(
        ["git", "-c", "user.name=agentkit-e2e", "-c", "user.email=e2e@example.invalid", *args],
        cwd=cwd, env=os.environ | dated, capture_output=True, text=True, check=True,
    )
    return done.stdout.strip()


def job_directory(tmp_path: Path, name: str, record: list[dict]) -> Path:
    """The working directory of a new job: the versioned files, `readings.jsonl` holding `record`, and an empty ledger."""
    directory = tmp_path / name
    directory.mkdir()
    for versioned in ("budget.json", "stops.json"):
        shutil.copyfile(ROOT / versioned, directory / versioned)
    (directory / "readings.jsonl").write_text("".join(json.dumps(item) + "\n" for item in record), encoding="utf-8")
    return directory


def evaluate_in(cwd: Path, workload_dir: Path, out: Path, candidate: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """`agentkit evaluate` in its own process, run from `cwd` as a job runs it from the root of its checkout."""
    return subprocess.run(evaluate_command(workload_dir, out, candidate), cwd=cwd, env=environment(env), capture_output=True, text=True, timeout=600)


def settings_digest(fixture: Path) -> str:
    return readings.digest(ScriptedAdapter(fixture).settings)


def dated_workload(workload_dir: Path, tmp_path: Path, candidate: str, made: str) -> Path:
    """A copy of the Workload in a git repository of its own, committed at `WORKLOAD_MADE`, then the test-only fixture `answers-CANDIDATE.jsonl` committed at `made`.

    The fixture's one line answers no Case of the twin, so that every held-out pair is missing, as for the twin's own fixtures."""
    copy = tmp_path / "a8r-examples" / workload_dir.name
    shutil.copytree(workload_dir, copy)
    git(copy.parent, "init", "--quiet")
    git(copy.parent, "add", "--all")
    git(copy.parent, "commit", "--quiet", "-m", "a8r: the Workload copy", date=WORKLOAD_MADE)
    line = {"case_id": "a8r-no-such-case", "repeat": 0, "observation_status": "produced", "raw_answer": "An answer the test wrote.", "settings": {"configuration": candidate}}
    (copy / "fixtures" / f"answers-{candidate}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    git(copy.parent, "add", "--all")
    git(copy.parent, "commit", "--quiet", "-m", f"a8r: the fixture {candidate}", date=made)
    return copy


def first_reading_in_kit(cwd: Path, workload_dir: Path) -> dict:
    """Makes `cwd` a checkout of the kit with one commit, dated `FIRST_READING`, and returns the record line of a reading of `concise` at that commit."""
    git(cwd, "init", "--quiet")
    git(cwd, "add", "budget.json", "stops.json")
    git(cwd, "commit", "--quiet", "-m", "a8r: the kit at its first reading", date=FIRST_READING)
    return readings.line("concise", settings_digest(workload_dir / "fixtures" / "answers-concise.jsonl"), git(cwd, "rev-parse", "HEAD"), "completed")


def reading_line(out: Path) -> list[dict]:
    return [json.loads(text) for text in (out / readings.LINE_FILE).read_text(encoding="utf-8").splitlines() if text.strip()]


def test_a_reading_publishes_its_line_for_the_record_with_the_candidate_its_configuration_digest_the_job_commit_and_completed(workload_dir, tmp_path):
    hypotrail_command()
    out = tmp_path / "a8r-published"
    done = evaluate_in(tmp_path, workload_dir, out, "concise")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [path.name for path in published(out)] == ["aggregate.json", "aggregate.md", "reading.jsonl"]
    assert reading_line(out) == [
        {"candidate": "concise", "configuration_digest": settings_digest(workload_dir / "fixtures" / "answers-concise.jsonl"), "job_commit": JOB_SHA, "status": "completed"}
    ]


def test_a_failed_reading_publishes_its_line_for_the_record_failed_and_no_aggregate(workload_dir, tmp_path, monkeypatch):
    job(monkeypatch)
    monkeypatch.setattr(conductor, "hypotrail_command", lambda: [sys.executable, "-c", "import sys; sys.exit(1)"])
    out = tmp_path / "a8r-published"
    assert main(["evaluate", "--workload-dir", str(workload_dir), "--candidate", "concise", "--out", str(out)]) == 1
    assert [path.name for path in published(out)] == ["reading.jsonl"]
    assert [(item["candidate"], item["job_commit"], item["status"]) for item in reading_line(out)] == [("concise", JOB_SHA, "failed")]


def test_a_candidate_already_in_the_committed_record_exits_8_in_a_job_with_an_empty_ledger_and_publishes_nothing(workload_dir, tmp_path):
    hypotrail_command()
    first_job = job_directory(tmp_path, "a8r-job-1", [])
    first = evaluate_in(first_job, workload_dir, tmp_path / "a8r-first", "concise")
    assert first.returncode == 0, first.stdout + first.stderr
    # A person commits the first job's line; the next job runs on a new runner, whose ledger is empty.
    second_job = job_directory(tmp_path, "a8r-job-2", reading_line(tmp_path / "a8r-first"))
    second = evaluate_in(second_job, workload_dir, tmp_path / "a8r-second", "concise")
    assert second.returncode == 8, second.stdout + second.stderr
    assert published(tmp_path / "a8r-second") == []
    assert [(record["status"], record.get("reason"), record["pairs"]) for record in read_records(second_job / "ledger")] == [("refused", "held_out_readings", [])]


def test_a_candidate_whose_fixture_was_committed_after_the_first_readings_job_commit_is_refused_with_exit_8_and_publishes_nothing(workload_dir, tmp_path):
    copy = dated_workload(workload_dir, tmp_path, "a8r-late", made=AFTER_IT)
    kit = job_directory(tmp_path, "a8r-job", [])
    (kit / "readings.jsonl").write_text(json.dumps(first_reading_in_kit(kit, workload_dir)) + "\n", encoding="utf-8")
    done = evaluate_in(kit, copy, tmp_path / "a8r-published", "a8r-late")
    assert done.returncode == 8, done.stdout + done.stderr
    assert published(tmp_path / "a8r-published") == []
    assert [(record["status"], record.get("reason"), record["pairs"]) for record in read_records(kit / "ledger")] == [("refused", readings.MADE_AFTER, [])]


def test_a_candidate_whose_fixture_was_committed_before_the_first_readings_job_commit_is_read(workload_dir, tmp_path):
    hypotrail_command()
    copy = dated_workload(workload_dir, tmp_path, "a8r-early", made=BEFORE_IT)
    kit = job_directory(tmp_path, "a8r-job", [])
    (kit / "readings.jsonl").write_text(json.dumps(first_reading_in_kit(kit, workload_dir)) + "\n", encoding="utf-8")
    done = evaluate_in(kit, copy, tmp_path / "a8r-published", "a8r-early")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [(item["candidate"], item["status"]) for item in reading_line(tmp_path / "a8r-published")] == [("a8r-early", "completed")]


@pytest.mark.parametrize("undated", ["shallow-hypotrail-checkout", "fixture-changed-in-the-working-tree", "job-commit-not-in-the-kit-checkout"])
def test_with_a_reading_in_the_record_a_candidate_the_checkouts_cannot_date_is_refused_with_exit_2_and_records_nothing(workload_dir, tmp_path, undated):
    copy = dated_workload(workload_dir, tmp_path, "a8r-early", made=BEFORE_IT)
    kit = job_directory(tmp_path, "a8r-job", [])
    first = first_reading_in_kit(kit, workload_dir)
    if undated == "shallow-hypotrail-checkout":
        shallow = tmp_path / "a8r-shallow"
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", f"file://{copy.parent}", str(shallow)], check=True, capture_output=True)
        copy = shallow / copy.name
    if undated == "fixture-changed-in-the-working-tree":
        with (copy / "fixtures" / "answers-a8r-early.jsonl").open("a", encoding="utf-8") as fixture:
            fixture.write("\n")
    if undated == "job-commit-not-in-the-kit-checkout":
        first = first | {"job_commit": "c" * 40}
    (kit / "readings.jsonl").write_text(json.dumps(first) + "\n", encoding="utf-8")
    done = evaluate_in(kit, copy, tmp_path / "a8r-published", "a8r-early")
    assert done.returncode == 2, done.stdout + done.stderr
    assert read_records(kit / "ledger") == []
    assert published(tmp_path / "a8r-published") == []


def test_a_malformed_record_refuses_the_reading_with_exit_2_before_it_spends(workload_dir, tmp_path, monkeypatch):
    job(monkeypatch)
    Path("readings.jsonl").write_text(json.dumps({"candidate": "concise", "case_id": "a8r-no-such-case"}) + "\n", encoding="utf-8")
    out = tmp_path / "a8r-published"
    assert main(["evaluate", "--workload-dir", str(workload_dir), "--candidate", "concise", "--out", str(out)]) == 2
    assert read_records() == []
    assert published(out) == []
