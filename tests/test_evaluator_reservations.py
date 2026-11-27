"""Durable evaluator permits using synthetic metadata and local Git history only."""

import json
from pathlib import Path

import pytest
import yaml

from agentkit import budget, readings, reservations
from conftest import git, reservation_history

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "example/kit"
JOB_COMMIT = "a" * 40
HYPOTRAIL_COMMIT = "b" * 40


def job_run(number):
    return f"https://github.com/{REPOSITORY}/actions/runs/{number}"


def reserve(name, number, *, digest=None):
    return {
        "kind": "reserve",
        "candidate": name,
        "configuration_digest": digest or readings.digest({"fixture": name}),
        "job_commit": JOB_COMMIT,
        "hypotrail_commit": HYPOTRAIL_COMMIT,
        "job_run": job_run(number),
    }


@pytest.fixture
def history(tmp_path):
    directory = tmp_path / "main-budget"
    return directory, reservation_history(directory)


def authorise(directory, *, name="first", number=1, attempt="1", job_commit=JOB_COMMIT, hypotrail_commit=HYPOTRAIL_COMMIT):
    return reservations.authorise(
        directory, budget.load(), candidate=name, run=job_run(number), attempt=attempt,
        job_commit=job_commit, hypotrail_commit=hypotrail_commit,
    )


def test_only_the_named_first_attempt_of_the_frozen_dispatch_can_use_the_permit(history):
    directory, append = history
    with pytest.raises(budget.BudgetRefused, match="reservation"):
        authorise(directory)
    append(reserve("first", 1))
    permit = authorise(directory)
    assert permit.configuration_digest == reserve("first", 1)["configuration_digest"]
    for change in ({"attempt": "2"}, {"number": 2}, {"name": "another"}, {"job_commit": "c" * 40}, {"hypotrail_commit": "d" * 40}):
        with pytest.raises(budget.BudgetRefused):
            authorise(directory, **change)


def test_the_last_place_is_counted_even_when_the_runner_never_left_an_artifact(history):
    directory, append = history
    for number, name in enumerate(("first", "second", "third"), start=1):
        append(reserve(name, number))
    assert authorise(directory, name="third", number=3).configuration_digest == reserve("third", 3)["configuration_digest"]
    with pytest.raises(budget.BudgetRefused) as refusal:
        authorise(directory, name="fourth", number=4)
    assert refusal.value.reason == "held_out_total_readings"
    with pytest.raises(budget.BudgetRefused) as refusal:
        authorise(directory, name="first", number=4)
    assert refusal.value.reason == "held_out_readings"


def test_a_stale_reservation_does_not_poison_replay_or_rejoin_after_a_return(history, monkeypatch):
    directory, append = history
    append(reserve("first", 1))
    append(reserve("second", 2))
    append(reserve("third", 3))
    # Two independently prepared PRs both claimed the last place.
    append(reserve("fourth", 4))
    assert authorise(directory, name="third", number=3).configuration_digest == reserve("third", 3)["configuration_digest"]
    with pytest.raises(budget.BudgetRefused) as refusal:
        authorise(directory, name="fourth", number=4)
    assert refusal.value.reason == "held_out_total_readings"
    owner_return = append({"kind": "return", "job_run": job_run(1)})
    simulate_github(monkeypatch, merge_commit=owner_return)
    append(reserve("fourth", 4))
    append(reserve("altered", 4))
    append(reserve("fifth", 5))
    assert authorise(directory, name="fifth", number=5).configuration_digest == reserve("fifth", 5)["configuration_digest"]
    for name in ("fourth", "altered"):
        with pytest.raises(budget.BudgetRefused) as refusal:
            authorise(directory, name=name, number=4)
        assert refusal.value.reason == "held_out_total_readings"


def test_a_configuration_renamed_in_a_concurrent_request_cannot_authorise_a_read(history):
    directory, append = history
    first = reserve("first", 1)
    append(first)
    append(reserve("second", 2, digest=first["configuration_digest"]))
    append(reserve("third", 3))
    assert authorise(directory, name="first", number=1).configuration_digest == first["configuration_digest"]
    assert authorise(directory, name="third", number=3).configuration_digest == reserve("third", 3)["configuration_digest"]
    with pytest.raises(budget.BudgetRefused) as refusal:
        authorise(directory, name="second", number=2)
    assert refusal.value.reason == "held_out_readings"


def test_worktree_metadata_and_rewritten_history_do_not_create_permits(history):
    directory, append = history
    record = directory / "reservations.jsonl"
    record.write_text(json.dumps(reserve("first", 1)) + "\n", encoding="utf-8")
    with pytest.raises(budget.BudgetRefused, match="reservation"):
        authorise(directory)
    record.write_text("", encoding="utf-8")
    append(reserve("first", 1))
    record.write_text(json.dumps(reserve("second", 2)) + "\n", encoding="utf-8")
    git(directory, "add", record.name)
    git(directory, "commit", "--quiet", "-m", "Rewrite the history")
    with pytest.raises(readings.RecordError, match="append"):
        authorise(directory, name="second", number=2)


def test_a_frozen_candidate_is_compared_to_the_first_snapshot_without_reading_results(tmp_path):
    work = tmp_path / "examples" / "workload"
    fixtures = work / "fixtures"
    fixtures.mkdir(parents=True)
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    (fixtures / "answers-first.jsonl").write_text("synthetic\n", encoding="utf-8")
    git(tmp_path, "add", "examples")
    git(tmp_path, "commit", "--quiet", "-m", "Freeze the first Candidate")
    first = git(tmp_path, "rev-parse", "HEAD")
    (fixtures / "answers-later.jsonl").write_text("synthetic\n", encoding="utf-8")
    git(tmp_path, "add", "examples")
    git(tmp_path, "commit", "--quiet", "-m", "Add a later Candidate")
    reservations.verify_frozen_fixture(work, "first", first)
    with pytest.raises(budget.BudgetRefused) as refusal:
        reservations.verify_frozen_fixture(work, "later", first)
    assert refusal.value.reason == "made_after_first_reading"
    (fixtures / "answers-first.jsonl").write_text("synthetic uncommitted edit\n", encoding="utf-8")
    with pytest.raises(readings.RecordError, match="frozen Git checkout"):
        reservations.verify_frozen_fixture(work, "first", first)


def test_a_frozen_fixture_alias_is_refused_before_accessing_its_bytes(tmp_path, monkeypatch):
    work = tmp_path / "examples" / "workload"
    fixtures = work / "fixtures"
    fixtures.mkdir(parents=True)
    git(tmp_path, "init", "--quiet", "--initial-branch=main")
    for name in ("first", "alias"):
        (fixtures / f"answers-{name}.jsonl").write_text("identical synthetic bytes\n", encoding="utf-8")
    git(tmp_path, "add", "examples")
    git(tmp_path, "commit", "--quiet", "-m", "Freeze a Candidate and an identical alias")
    first = git(tmp_path, "rev-parse", "HEAD")
    inspect_git = reservations._git

    def only_git_objects(directory, *args):
        assert args[0] in {"rev-parse", "cat-file"}
        return inspect_git(directory, *args)

    monkeypatch.setattr(reservations, "_git", only_git_objects)
    with pytest.raises(budget.BudgetRefused) as refusal:
        reservations.verify_frozen_fixture(work, "alias", first, ("first",))
    assert refusal.value.reason == "held_out_readings"


def simulate_github(monkeypatch, *, merge_commit, actor=7, finished="2026-11-27T16:00:00Z", status="completed",
                    merge_sha=None, number=80, merged_at="2026-11-27T17:00:00Z"):
    requests = []
    responses = {
        f"/repos/{REPOSITORY}": {"owner": {"id": 7}},
        f"/repos/{REPOSITORY}/commits/{merge_commit}/pulls": [{"number": number, "merge_commit_sha": merge_commit}],
        f"/repos/{REPOSITORY}/pulls/{number}": {
            "merged": True, "merge_commit_sha": merge_sha or merge_commit, "merged_at": merged_at,
            "merged_by": {"id": actor}, "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
        },
        f"/repos/{REPOSITORY}/actions/runs/1/attempts/1": {
            "id": 1, "run_attempt": 1, "head_sha": JOB_COMMIT, "status": status, "updated_at": finished,
        },
    }

    previous = reservations._github_json

    def get(path):
        requests.append(path)
        return responses[path] if path in responses else previous(path)

    monkeypatch.setattr(reservations, "_github_json", get)
    return requests


def test_an_owner_merged_return_frees_total_capacity_and_keeps_the_one_candidate_guard(history, monkeypatch):
    directory, append = history
    for number, name in enumerate(("first", "second", "third"), start=1):
        append(reserve(name, number))
    commit = append({"kind": "return", "job_run": job_run(1)})
    requests = simulate_github(monkeypatch, merge_commit=commit)
    append(reserve("fourth", 4))
    permit = authorise(directory, name="fourth", number=4)
    assert permit.configuration_digest == reserve("fourth", 4)["configuration_digest"]
    assert f"/repos/{REPOSITORY}/actions/runs/1/attempts/1" in requests
    for number in (1, 5):
        with pytest.raises(budget.BudgetRefused):
            authorise(directory, number=number)


@pytest.mark.parametrize("failure", ["other-merger", "wrong-merge", "still-running", "ended-after-merge", "same-second"])
def test_an_unauthorised_or_premature_return_grants_no_capacity(history, monkeypatch, failure):
    directory, append = history
    for number, name in enumerate(("first", "second", "third"), start=1):
        append(reserve(name, number))
    commit = append({"kind": "return", "job_run": job_run(1)})
    options = {
        "other-merger": {"actor": 8}, "wrong-merge": {"merge_sha": "d" * 40},
        "still-running": {"status": "in_progress"}, "ended-after-merge": {"finished": "2026-11-27T18:00:00Z"},
        "same-second": {"finished": "2026-11-27T17:00:00Z"},
    }
    simulate_github(monkeypatch, merge_commit=commit, **options[failure])
    append(reserve("fourth", 4))
    with pytest.raises(budget.BudgetRefused) as refusal:
        authorise(directory, name="fourth", number=4)
    assert refusal.value.reason == "held_out_total_readings"
    assert authorise(directory, name="third", number=3).configuration_digest == reserve("third", 3)["configuration_digest"]


def test_invalid_returns_do_not_block_a_later_authenticated_return(history, monkeypatch):
    directory, append = history
    for number, name in enumerate(("first", "second", "third"), start=1):
        append(reserve(name, number))
    event = {"kind": "return", "job_run": job_run(1)}
    other_merger = append(event)
    simulate_github(monkeypatch, merge_commit=other_merger, actor=8)
    too_early = append(event)
    simulate_github(monkeypatch, merge_commit=too_early, finished="2026-11-27T18:00:00Z", number=81)
    owner = append(event)
    simulate_github(monkeypatch, merge_commit=owner, finished="2026-11-27T18:00:00Z", merged_at="2026-11-27T19:00:00Z", number=82)
    append(reserve("fourth", 4))
    assert authorise(directory, name="fourth", number=4).configuration_digest == reserve("fourth", 4)["configuration_digest"]


@pytest.mark.parametrize("evidence", ["missing", "unqualified-time"])
def test_unavailable_merge_evidence_closes_the_gate(history, monkeypatch, evidence):
    directory, append = history
    append(reserve("first", 1))
    owner_return = append({"kind": "return", "job_run": job_run(1)})
    if evidence == "missing":
        monkeypatch.setattr(reservations, "_github_json", lambda path: {})
    else:
        simulate_github(monkeypatch, merge_commit=owner_return, finished="2026-11-27T16:00:00", merged_at="2026-11-27T17:00:00")
    with pytest.raises(readings.RecordError, match="evidence"):
        authorise(directory)


def test_the_evaluator_reads_the_main_snapshot_before_the_protected_workload_without_write_permissions():
    workflow = yaml.safe_load((ROOT / ".github/workflows/evaluator.yml").read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["held-out-reading"]["steps"]
    ledger = [index for index, step in enumerate(steps) if step.get("with", {}).get("path") == "budget-checkout"]
    preflight = [index for index, step in enumerate(steps) if "agentkit verify-reservation" in step.get("run", "")]
    private = [index for index, step in enumerate(steps) if step.get("with", {}).get("path") == "hypotrail"]
    assert len(ledger) == len(preflight) == len(private) == 1
    assert ledger[0] < preflight[0] < private[0]
    assert steps[ledger[0]]["with"] == {"ref": "refs/heads/main", "path": "budget-checkout", "fetch-depth": 0, "persist-credentials": False}
    evaluate = next(step for step in steps if "agentkit evaluate" in step.get("run", ""))
    assert "--reservations-checkout budget-checkout" in evaluate["run"]
