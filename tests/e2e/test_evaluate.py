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
from contextlib import contextmanager
from pathlib import Path

import jsonschema
import pytest

from agentkit import conductor, decisions, evaluator_job, readings
from agentkit.adapters.scripted import ScriptedAdapter
from agentkit.cases import WorkloadError
from agentkit.cli import main
from agentkit.ledger import read_records
from conftest import git, reservation_history
from test_conduct import files_holding
from test_dev_end_to_end import ROOT, agentkit_command, hypotrail_command, kit_clone

SCHEMA = ROOT / "contracts" / "kit-aggregate-1" / "aggregate.schema.json"
JOB_SHA = "a8" * 20
HELD_OUT_FIXTURE = "a8-held-out"
# What GitHub Actions sets in a job of the evaluator workflow, imitated.
JOB = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_WORKFLOW_REF": "chadlis/agentkit/.github/workflows/evaluator.yml@refs/heads/main",
    "GITHUB_SHA": JOB_SHA,
    "GITHUB_REPOSITORY": "chadlis/agentkit",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_RUN_ID": "123",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_ACTOR": "a-person",
}


def environment(extra: dict | None = None) -> dict:
    return {key: value for key, value in os.environ.items() if key != "HYPOTRAIL_ANTHROPIC_API_KEY"} | JOB | (extra or {})


@contextmanager
def evaluate_arguments(workload_dir: Path, out: Path, candidate: str):
    with tempfile.TemporaryDirectory(prefix="agentkit-test-reservation-") as scratch:
        checkout = Path(scratch) / "budget-checkout"
        append = reservation_history(checkout)
        fixture = workload_dir / "fixtures" / f"answers-{candidate}.jsonl"
        if conductor.CONFIGURATION.fullmatch(candidate):
            try:
                settings = ScriptedAdapter(fixture).settings
            except WorkloadError:
                # The actual evaluator still checks the invalid fixture and its output boundary.
                settings = None
            append({
                "kind": "reserve", "candidate": candidate, "configuration_digest": readings.digest(settings),
                "job_commit": JOB_SHA, "hypotrail_commit": evaluator_job.checkout_commit(workload_dir),
                "job_run": "https://github.com/chadlis/agentkit/actions/runs/123",
            })
        yield [
            "evaluate", "--workload-dir", str(workload_dir), "--candidate", candidate, "--out", str(out),
            "--reservations-checkout", str(checkout),
        ]


def evaluate(workload_dir: Path, out: Path, candidate: str = "concise", env: dict | None = None) -> subprocess.CompletedProcess:
    """`agentkit evaluate` in its own process, in the working directory of the test, as a job of `evaluator.yml` would run it."""
    with evaluate_arguments(workload_dir, out, candidate) as arguments:
        return subprocess.run([*agentkit_command(), *arguments], env=environment(env), capture_output=True, text=True, timeout=600)


def evaluate_in_process(workload_dir: Path, out: Path, candidate: str = "concise") -> int:
    with evaluate_arguments(workload_dir, out, candidate) as arguments:
        return main(arguments)


def json_lines(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines() if text.strip()]


def held_out_cases(workload_dir: Path) -> tuple[list[dict], dict[str, dict]]:
    """The held-out Cases, and their References by Case id, as the twin holds them."""
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    cases = json_lines(workload_dir / workload["cases"]["public_check"])
    held_out = {case["case_id"] for case in cases}
    return cases, {reference["case_id"]: reference for reference in json_lines(workload_dir / workload["references"]) if reference["case_id"] in held_out}


def entity_ids(reference: dict) -> list[str]:
    """The entity ids a Reference expects, as an answer names them: `album 1`."""
    return [f"{item['kind']} {item['value']}" for item in reference["expected_identifiers"]]


def held_out_texts(workload_dir: Path) -> list[str]:
    """Every held-out Case id, question, Reference text and entity id of a Reference."""
    cases, references = held_out_cases(workload_dir)
    entities = sorted({entity for reference in references.values() for entity in entity_ids(reference)})
    return [case["case_id"] for case in cases] + [case["question"] for case in cases] + [reference["reference_text"] for reference in references.values()] + entities


def held_out_workload(workload_dir: Path, tmp_path: Path) -> tuple[Path, list[str]]:
    """A copy of the Workload, committed in a git repository of its own, with a test-only fixture, `fixtures/answers-a8-held-out.jsonl`.

    The fixture holds a produced answer for every held-out Case, naming the entity ids its Reference expects, so that answers flow through the job. Returns the copy and the answers."""
    copy = tmp_path / "a8-examples" / workload_dir.name
    shutil.copytree(workload_dir, copy)
    cases, references = held_out_cases(workload_dir)
    answers, lines = [], []
    for number, case in enumerate(cases, start=1):
        named = entity_ids(references[case["case_id"]])
        answers.append(f"Held-out answer a8-{number:02d}, written by the test" + (f", naming {' and '.join(named)}." if named else "."))
        lines.append({"case_id": case["case_id"], "repeat": 0, "observation_status": "produced", "raw_answer": answers[-1], "outcome": "answered", "settings": {"configuration": HELD_OUT_FIXTURE}})
    (copy / "fixtures" / f"answers-{HELD_OUT_FIXTURE}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    for command in (["init", "--quiet"], ["add", "--all"], ["commit", "--quiet", "-m", "a8: the Workload copy and its held-out fixture"]):
        subprocess.run(["git", "-c", "user.name=agentkit-e2e", "-c", "user.email=e2e@example.invalid", *command], cwd=copy.parent, check=True, capture_output=True)
    return copy, answers


def fake_hypotrail(tmp_path: Path, script: str) -> dict:
    """The environment under which `python -m hypotrail` runs `script` instead of Hypotrail."""
    package = tmp_path / "a8-fake-hypotrail" / "hypotrail"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(script, encoding="utf-8")
    return {"PYTHONPATH": str(package.parent)}


def kit_files() -> list[Path]:
    """Every file `git status` lists in the clone of the kit the test runs in, ignored ones included."""
    listed = subprocess.run(["git", "status", "--porcelain", "--ignored", "--untracked-files=all"], capture_output=True, text=True, check=True).stdout.splitlines()
    return [Path(line[3:]) for line in listed]


def published(out: Path) -> list[Path]:
    return sorted(path for path in out.rglob("*") if path.is_file())


def checkout_commit(workload_dir: Path) -> str:
    return subprocess.run(["git", "-C", str(workload_dir), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def test_a_reading_in_an_imitated_evaluator_job_publishes_an_aggregate_report_that_counts_the_6_held_out_cases(workload_dir, tmp_path):
    hypotrail_command()
    out = tmp_path / "a8-published"
    done = evaluate(workload_dir, out)
    assert done.returncode == 0, done.stdout + done.stderr
    assert [path.name for path in published(out)] == ["aggregate.json", "aggregate.md", "reading.jsonl"]
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


def test_no_published_file_holds_a_held_out_question_case_id_reference_text_entity_id_or_answer(workload_dir, tmp_path):
    hypotrail_command()
    copy, answers = held_out_workload(workload_dir, tmp_path)
    needles = held_out_texts(workload_dir) + answers
    found = len(needles)
    assert found == 26
    out = tmp_path / "a8-published"
    done = evaluate(copy, out, candidate=HELD_OUT_FIXTURE)
    assert done.returncode == 0, done.stdout + done.stderr
    files = published(out)
    assert len(files) == 3
    # The answers went through the job: every held-out pair is produced.
    assert json.loads((out / "aggregate.json").read_text(encoding="utf-8"))["counts"] == {"units": 6, "produced": 6, "failed": 0, "missing": 0}
    holding = files_holding(needles, files)
    assert holding == 0


def test_after_a_reading_no_file_left_in_the_kit_or_in_its_temporary_directory_holds_held_out_text(workload_dir, tmp_path, monkeypatch):
    hypotrail_command()
    copy, answers = held_out_workload(workload_dir, tmp_path)
    needles = held_out_texts(workload_dir) + answers
    # The job runs from the root of a clone of the kit, so that every file it writes there is one `git status` lists, the published ones included.
    work = kit_clone(tmp_path)
    monkeypatch.chdir(work)
    temporary = tmp_path.parent / f"{tmp_path.name}-a8-tmp"
    temporary.mkdir()
    done = evaluate(copy, work / "a8-published", candidate=HELD_OUT_FIXTURE, env={"TMPDIR": str(temporary)})
    assert done.returncode == 0, done.stdout + done.stderr
    written = kit_files()
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


def test_a_reading_whose_hypotrail_fails_exits_1_spends_the_reading_publishes_its_record_and_removes_its_temporary_directory(workload_dir, tmp_path, monkeypatch):
    job(monkeypatch)
    temporary = tmp_path.parent / f"{tmp_path.name}-a8-tmp"
    temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    monkeypatch.setattr(conductor, "hypotrail_command", lambda: [sys.executable, "-c", "import sys; sys.exit(1)"])
    out = tmp_path / "a8-published"
    assert evaluate_in_process(workload_dir, out) == 1
    assert [(record["set"], record["status"], record.get("reason")) for record in read_records()] == [("public_check", "failed", "hypotrail")]
    assert [path.name for path in published(out)] == ["reading.jsonl"]
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
    assert evaluate_in_process(workload_dir, out, candidate) == 2
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
    assert evaluate_in_process(copy, out, candidate) == 2
    assert read_records() == []
    assert published(out) == []


def test_a_reading_sent_sigterm_removes_its_temporary_directory_and_closes_the_reading_failed(workload_dir, tmp_path):
    hypotrail_command()
    temporary = tmp_path.parent / f"{tmp_path.name}-a8-tmp"
    temporary.mkdir()
    with evaluate_arguments(workload_dir, tmp_path / "a8-published", "concise") as arguments:
        process = subprocess.Popen(
            [*agentkit_command(), *arguments], env=environment({"TMPDIR": str(temporary)}),
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
    assert [(record["status"], record.get("reason")) for record in read_records()] == [("failed", "interrupted")]
    assert [path.name for path in published(tmp_path / "a8-published")] == ["reading.jsonl"]


def hypotrail_failing_at(tmp_path: Path, failing: str, stderr: str, assessment: dict | None = None) -> dict:
    """The environment under which a fake `python -m hypotrail` answers each command of the job as Hypotrail would, until `failing`, where it writes `stderr` and exits 2."""
    printed = {"init": {"references_id": "a8-references"}, "import": {"observation_set_id": "a8-observations"}, "assess": {"assessment": assessment or {}}, "report": {}}
    return fake_hypotrail(tmp_path, f"""import json, sys
if sys.argv[1] == {failing!r}:
    sys.stderr.write({stderr!r})
    sys.exit(2)
print(json.dumps({printed!r}[sys.argv[1]]))
""")


@pytest.mark.parametrize("failing", ["init", "import", "assess", "report"])
def test_a_hypotrail_failure_whose_stderr_names_a_held_out_case_shows_it_in_no_output_of_the_job_and_no_file(workload_dir, tmp_path, monkeypatch, failing):
    case_id = held_out_cases(workload_dir)[0][0]["case_id"]
    # Hypotrail names a Case in what it prints on stderr, as `import agent` does for a Case it does not know.
    environment_of_job = hypotrail_failing_at(tmp_path, failing, f"{case_id} is not a Case\n")
    work = kit_clone(tmp_path)
    monkeypatch.chdir(work)
    temporary = tmp_path.parent / f"{tmp_path.name}-a8-tmp"
    temporary.mkdir()
    done = evaluate(workload_dir, work / "a8-published", env={"TMPDIR": str(temporary), **environment_of_job})
    exit_code = done.returncode
    assert exit_code == 1
    shown = [name for name, text in (("stdout", done.stdout), ("stderr", done.stderr)) if case_id in text]
    assert shown == []
    holding = files_holding([case_id], kit_files())
    assert holding == 0
    assert list(temporary.iterdir()) == []


def test_per_criterion_aggregates_hypotrail_does_not_give_are_reported_by_the_error_type_only(workload_dir, tmp_path):
    case_id = held_out_cases(workload_dir)[0][0]["case_id"]
    # The Assessment names a per-Criterion file that is not there, whose path holds a held-out Case id.
    environment_of_job = hypotrail_failing_at(tmp_path, "none", "", assessment={"detailed_metrics_ref": {"path": f"{case_id}-metrics.json"}})
    done = evaluate(workload_dir, tmp_path / "a8-published", env=environment_of_job)
    assert [(record["status"], record.get("reason")) for record in read_records()] == [("failed", "hypotrail")]
    reported = "hypotrail assess gave no per-Criterion aggregates (FileNotFoundError)" in done.stderr
    shown = case_id in done.stdout + done.stderr
    assert (done.returncode, reported, shown) == (1, True, False)


def test_an_error_after_the_reading_is_counted_closes_it_failed_and_shows_no_held_out_case(workload_dir, tmp_path, monkeypatch, capsys):
    case_id = held_out_cases(workload_dir)[0][0]["case_id"]
    job(monkeypatch)
    temporary = tmp_path.parent / f"{tmp_path.name}-a8-tmp"
    temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))

    def failing_export(*args, **kwargs):
        raise WorkloadError(f"an error that names {case_id}")

    monkeypatch.setattr(evaluator_job, "export", failing_export)
    out = tmp_path / "a8-published"
    exit_code = evaluate_in_process(workload_dir, out)
    printed = capsys.readouterr()
    assert exit_code == 1
    assert [(record["set"], record["status"], record.get("reason")) for record in read_records()] == [("public_check", "failed", "error")]
    shown = (case_id in printed.out) + (case_id in printed.err)
    assert shown == 0
    assert [path.name for path in published(out)] == ["reading.jsonl"]
    assert list(temporary.iterdir()) == []


def test_a_key_error_after_the_reading_is_counted_closes_it_failed_with_no_traceback(workload_dir, tmp_path):
    # Hypotrail's `init` prints an object without the ids the job reads.
    done = evaluate(workload_dir, tmp_path / "a8-published", env=fake_hypotrail(tmp_path, "print('{}')\n"))
    assert [(record["set"], record["status"], record.get("reason")) for record in read_records()] == [("public_check", "failed", "error")]
    assert (done.returncode, "Traceback" in done.stderr) == (1, False)
    assert [path.name for path in published(tmp_path / "a8-published")] == ["reading.jsonl"]


def test_a_fixture_line_the_job_cannot_replay_is_refused_with_exit_2_without_naming_its_held_out_case(workload_dir, tmp_path, monkeypatch, capsys):
    case_id = held_out_cases(workload_dir)[0][0]["case_id"]
    job(monkeypatch)
    copy = tmp_path / "a8-examples" / workload_dir.name
    shutil.copytree(workload_dir, copy)
    (copy / "fixtures" / "answers-a8-bad.jsonl").write_text(json.dumps({"case_id": case_id, "repeat": 0, "observation_status": "produced"}) + "\n", encoding="utf-8")
    git(copy.parent, "init", "--quiet")
    git(copy.parent, "add", "--all")
    git(copy.parent, "commit", "--quiet", "-m", "Freeze the synthetic malformed fixture")
    exit_code = evaluate_in_process(copy, tmp_path / "a8-published", "a8-bad")
    printed = capsys.readouterr()
    assert exit_code == 2
    shown = (case_id in printed.out) + (case_id in printed.err)
    assert shown == 0
    assert read_records() == []
