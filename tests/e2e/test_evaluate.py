"""The end-to-end tests of the evaluator job, chadlis/agentkit#9: `agentkit evaluate` reads one frozen Configuration on the twin's held-out set, has Hypotrail at the pinned commit import, assess and report it inside the job, and publishes an aggregate report only.

The variables GitHub Actions sets in a job of `evaluator.yml` are imitated, as a local process can: the entry point is a guard, not a wall. Offline; no model call.

A failing assertion here shows counts and ids only, never a held-out question, answer or Reference text."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import jsonschema
import pytest

from agentkit import conductor, decisions, evaluator_job
from agentkit.cli import main
from agentkit.ledger import read_records
from test_conduct import files_holding
from test_dev_end_to_end import ROOT, agentkit_command, hypotrail_command, kit_clone

SCHEMA = ROOT / "contracts" / "kit-aggregate-1" / "aggregate.schema.json"
JOB_SHA = "a8" * 20
# What GitHub Actions sets in a job of the evaluator workflow, imitated.
JOB = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_WORKFLOW_REF": "chadlis/agentkit/.github/workflows/evaluator.yml@refs/heads/main",
    "GITHUB_SHA": JOB_SHA,
    "GITHUB_REPOSITORY": "chadlis/agentkit",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_RUN_ID": "123",
    "GITHUB_ACTOR": "a-person",
}


def environment(extra: dict | None = None) -> dict:
    return {key: value for key, value in os.environ.items() if key != "HYPOTRAIL_ANTHROPIC_API_KEY"} | JOB | (extra or {})


def evaluate_command(workload_dir: Path, out: Path, candidate: str) -> list[str]:
    return [*agentkit_command(), "evaluate", "--workload-dir", str(workload_dir), "--candidate", candidate, "--out", str(out)]


def evaluate(workload_dir: Path, out: Path, candidate: str = "concise", env: dict | None = None) -> subprocess.CompletedProcess:
    """`agentkit evaluate` in its own process, in the working directory of the test, as a job of `evaluator.yml` would run it."""
    return subprocess.run(evaluate_command(workload_dir, out, candidate), env=environment(env), capture_output=True, text=True, timeout=600)


def held_out_texts(workload_dir: Path) -> list[str]:
    """Every held-out Case id, question and Reference text, as the twin holds them."""
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    cases = [json.loads(text) for text in (workload_dir / workload["cases"]["public_check"]).read_text(encoding="utf-8").splitlines() if text.strip()]
    held_out = [case["case_id"] for case in cases]
    references = [json.loads(text) for text in (workload_dir / workload["references"]).read_text(encoding="utf-8").splitlines() if text.strip()]
    return held_out + [case["question"] for case in cases] + [reference["reference_text"] for reference in references if reference["case_id"] in held_out]


def published(out: Path) -> list[Path]:
    return sorted(path for path in out.rglob("*") if path.is_file())


def checkout_commit(workload_dir: Path) -> str:
    return subprocess.run(["git", "-C", str(workload_dir), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def test_a_reading_in_an_imitated_evaluator_job_publishes_an_aggregate_report_that_counts_the_6_held_out_cases(workload_dir, tmp_path):
    hypotrail_command()
    out = tmp_path / "a8-published"
    done = evaluate(workload_dir, out)
    assert done.returncode == 0, done.stdout + done.stderr
    assert [path.name for path in published(out)] == ["aggregate.json", "aggregate.md"]
    aggregate = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
    errors = [error.message for error in jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).iter_errors(aggregate)]
    assert errors == []
    # The twin's fixtures hold dev answers only: every held-out pair is missing.
    assert aggregate["counts"] == {"units": 6, "produced": 0, "failed": 0, "missing": 6}
    assert (aggregate["partition"], aggregate["kit"]) == ("public_check", {"repository": "chadlis/agentkit", "commit": JOB_SHA, "candidate": "concise"})
    assert aggregate["reading"] == {"number": 1, "allowed": 1, "job_run": "https://github.com/chadlis/agentkit/actions/runs/123", "actor": "a-person"}
    hypotrail = aggregate["hypotrail"]
    assert (hypotrail["commit"], len(hypotrail["observation_set_ids"]), len(hypotrail["assessment_ids"]), hypotrail["comparison_id"]) == (checkout_commit(workload_dir), 1, 1, None)
    assert aggregate["decision"] is None
    values = {item["name"]: (item["value"], item.get("n")) for item in aggregate["aggregates"]}
    assert values["criterion.text_facts_success.confirmed_share_of_expected"] == (0.0, 6)
    assert {name: values[f"criterion.{name}.unscored"] for name in ("facts_present", "identifiers_complete", "no_unexpected_identifier", "outcome_matches")} == dict.fromkeys(
        ("facts_present", "identifiers_complete", "no_unexpected_identifier", "outcome_matches"), (6, None)
    )
    # The kit's ledger counts the reading and lists none of its pairs; the job writes no decision.
    assert [(record["set"], record["status"], record["pairs"]) for record in read_records()] == [("public_check", "completed", [])]
    assert decisions.lines() == []


def test_no_published_file_holds_a_held_out_question_case_id_or_reference_text(workload_dir, tmp_path):
    hypotrail_command()
    needles = held_out_texts(workload_dir)
    found = len(needles)
    assert found == 18
    out = tmp_path / "a8-published"
    done = evaluate(workload_dir, out)
    assert done.returncode == 0, done.stdout + done.stderr
    files = published(out)
    assert len(files) == 2
    holding = files_holding(needles, files)
    assert holding == 0


def test_after_a_reading_no_file_left_in_the_kit_or_in_its_temporary_directory_holds_held_out_text(workload_dir, tmp_path, monkeypatch):
    hypotrail_command()
    needles = held_out_texts(workload_dir)
    # The job runs from the root of a clone of the kit, so that every file it writes there is one `git status` lists, the published ones included.
    work = kit_clone(tmp_path)
    monkeypatch.chdir(work)
    temporary = tmp_path.parent / f"{tmp_path.name}-a8-tmp"
    temporary.mkdir()
    done = evaluate(workload_dir, work / "a8-published", env={"TMPDIR": str(temporary)})
    assert done.returncode == 0, done.stdout + done.stderr
    listed = subprocess.run(["git", "status", "--porcelain", "--ignored", "--untracked-files=all"], capture_output=True, text=True, check=True).stdout.splitlines()
    written = [Path(line[3:]) for line in listed]
    assert {path.parts[0] for path in written} == {"ledger", "a8-published"}
    holding = files_holding(needles, written)
    assert holding == 0
    assert list(temporary.iterdir()) == []


def test_a_second_reading_of_the_same_configuration_is_refused_with_exit_8_and_publishes_nothing(workload_dir, tmp_path):
    hypotrail_command()
    first = evaluate(workload_dir, tmp_path / "a8-first")
    assert first.returncode == 0, first.stdout + first.stderr
    second = evaluate(workload_dir, tmp_path / "a8-second")
    assert second.returncode == 8, second.stdout + second.stderr
    assert [(record["status"], record.get("reason")) for record in read_records()] == [("completed", None), ("refused", "held_out_readings")]
    assert published(tmp_path / "a8-second") == []


def job(monkeypatch, extra: dict | None = None) -> None:
    for name, value in (JOB | (extra or {})).items():
        monkeypatch.setenv(name, value)


def test_a_reading_whose_hypotrail_fails_exits_1_spends_the_reading_publishes_nothing_and_removes_its_temporary_directory(workload_dir, tmp_path, monkeypatch):
    job(monkeypatch)
    temporary = tmp_path.parent / f"{tmp_path.name}-a8-tmp"
    temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    monkeypatch.setattr(conductor, "hypotrail_command", lambda: [sys.executable, "-c", "import sys; sys.exit(1)"])
    out = tmp_path / "a8-published"
    assert main(["evaluate", "--workload-dir", str(workload_dir), "--candidate", "concise", "--out", str(out)]) == 1
    assert [(record["set"], record["status"], record.get("reason")) for record in read_records()] == [("public_check", "failed", "hypotrail")]
    assert published(out) == []
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize(
    ("change", "candidate"),
    [
        pytest.param("out-not-empty", "concise", id="out-not-empty"),
        pytest.param("no-sha", "concise", id="no-job-commit"),
        pytest.param(None, "no-such-fixture", id="no-such-fixture"),
    ],
)
def test_a_reading_the_job_cannot_report_is_refused_with_exit_2_before_it_spends(workload_dir, tmp_path, monkeypatch, change, candidate):
    job(monkeypatch)
    out = tmp_path / "a8-published"
    if change == "out-not-empty":
        out.mkdir()
        (out / "left-over.txt").write_text("a file the job did not write\n", encoding="utf-8")
    if change == "no-sha":
        monkeypatch.delenv("GITHUB_SHA")
    assert main(["evaluate", "--workload-dir", str(workload_dir), "--candidate", candidate, "--out", str(out)]) == 2
    assert read_records() == []
    assert [path.name for path in published(out)] == (["left-over.txt"] if change == "out-not-empty" else [])


def test_a_candidate_that_is_not_a_fixture_name_is_refused_with_exit_2_even_when_a_fixture_file_has_that_name(workload_dir, tmp_path, monkeypatch):
    job(monkeypatch)
    # A copy of the Workload, outside a git checkout: the report would name the commit the test gives.
    copy = tmp_path / "a8-examples" / workload_dir.name
    shutil.copytree(workload_dir, copy)
    candidate = "a8 free text"
    shutil.copyfile(workload_dir / "fixtures" / "answers-concise.jsonl", copy / "fixtures" / f"answers-{candidate}.jsonl")
    monkeypatch.setattr(evaluator_job, "checkout_commit", lambda directory: "b" * 40)
    out = tmp_path / "a8-published"
    assert main(["evaluate", "--workload-dir", str(copy), "--candidate", candidate, "--out", str(out)]) == 2
    assert read_records() == []
    assert published(out) == []


def test_a_reading_sent_sigterm_removes_its_temporary_directory(workload_dir, tmp_path):
    hypotrail_command()
    temporary = tmp_path.parent / f"{tmp_path.name}-a8-tmp"
    temporary.mkdir()
    process = subprocess.Popen(
        evaluate_command(workload_dir, tmp_path / "a8-published", "concise"), env=environment({"TMPDIR": str(temporary)}),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 120
    # Mid-reading: once `hypotrail init` has copied the References into the temporary Study.
    while not list(temporary.glob("agentkit-evaluator-*/study/artifacts/*")) and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert process.poll() is None, "the reading ended before the temporary Study was made"
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=120)
    assert list(temporary.iterdir()) == []
    assert process.returncode == 128 + signal.SIGTERM
