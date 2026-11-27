"""The evaluator's durable, metadata-only spending record on Agentkit main."""

import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agentkit import budget, readings
from agentkit.conductor import CONFIGURATION

RECORD = "reservations.jsonl"
RUN = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/actions/runs/([1-9][0-9]*)")
RESERVE_KEYS = {"kind", "candidate", "configuration_digest", "job_commit", "hypotrail_commit", "job_run"}
RETURN_KEYS = {"kind", "job_run"}
API = "https://api.github.com"


@dataclass(frozen=True)
class Permit:
    configuration_digest: str
    first_hypotrail_commit: str


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", "-C", str(directory), *args], capture_output=True)
    except OSError:
        raise readings.RecordError("cannot read the committed reservation history") from None


def _history(directory: Path):
    """Read only the first-parent commits of the Agentkit main checkout, never its worktree file."""
    depth = _git(directory, "rev-parse", "--is-shallow-repository")
    if depth.returncode or depth.stdout != b"false\n":
        raise readings.RecordError("the complete committed reservation history is required")
    log = _git(directory, "log", "--first-parent", "--full-history", "--reverse", "--format=%H", "HEAD", "--", RECORD)
    if log.returncode or not log.stdout.strip():
        raise readings.RecordError("the complete committed reservation history is required")
    previous = b""
    for number, commit in enumerate(log.stdout.decode("ascii").splitlines(), start=1):
        change = _git(directory, "show", f"{commit}:{RECORD}")
        if change.returncode or not change.stdout.startswith(previous):
            raise readings.RecordError("the committed reservation history is append-only")
        added = change.stdout[len(previous):]
        previous = change.stdout
        if number == 1:
            if added:
                raise readings.RecordError("the committed reservation history must start empty")
            continue
        if not added.endswith(b"\n") or added.count(b"\n") != 1:
            raise readings.RecordError("append exactly one reservation event per main commit")
        try:
            event = json.loads(added)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise readings.RecordError("a reservation event is not valid JSON") from None
        if not _well_formed(event):
            raise readings.RecordError("a reservation event contains invalid metadata")
        yield commit, event


def _well_formed(event) -> bool:
    if not isinstance(event, dict) or not all(isinstance(value, str) for value in event.values()) or not RUN.fullmatch(event.get("job_run", "")):
        return False
    if event.get("kind") == "return":
        return event.keys() == RETURN_KEYS
    return (
        event.get("kind") == "reserve" and event.keys() == RESERVE_KEYS
        and CONFIGURATION.fullmatch(event["candidate"]) is not None
        and readings.DIGEST.fullmatch(event["configuration_digest"]) is not None
        and readings.COMMIT.fullmatch(event["job_commit"]) is not None
        and readings.COMMIT.fullmatch(event["hypotrail_commit"]) is not None
    )


def _github_json(path: str):
    request = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "agentkit-evaluator"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except (OSError, UnicodeDecodeError, ValueError):
        raise readings.RecordError("GitHub could not verify a returned reservation") from None


def _verified_return(repository: str, commit: str, reservation: dict, run_number: str) -> bool:
    route = f"/repos/{repository}"
    try:
        owner = _github_json(route)["owner"]["id"]
        associated = _github_json(f"{route}/commits/{commit}/pulls")
        number, = [pull["number"] for pull in associated if pull["merge_commit_sha"] == commit]
        if type(number) is not int or number <= 0 or type(owner) is not int or owner <= 0:
            return False
        pull = _github_json(f"{route}/pulls/{number}")
        if not (
            pull["merged"] is True and pull["merge_commit_sha"] == commit and pull["merged_by"]["id"] == owner
            and pull["base"]["ref"] == "main" and pull["base"]["repo"]["full_name"] == repository
        ):
            return False
        run = _github_json(f"{route}/actions/runs/{run_number}/attempts/1")
        return (
            run["status"] == "completed" and run["id"] == int(run_number) and run["run_attempt"] == 1
            and run["head_sha"] == reservation["job_commit"]
            and datetime.fromisoformat(run["updated_at"]) <= datetime.fromisoformat(pull["merged_at"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def authorise(
    directory: Path, limits: budget.Budget, *, candidate: str, run: str, attempt: str,
    job_commit: str, hypotrail_commit: str,
) -> Permit:
    """Replay the merged record and admit only the one frozen dispatch that owns a reservation."""
    identity = RUN.fullmatch(run)
    if identity is None:
        raise readings.RecordError("the evaluator must name a GitHub workflow run")
    repository = identity[1]
    if attempt != "1":
        raise budget.BudgetRefused("reading_reservation", "a reservation authorises only the first dispatch attempt")
    if limits.held_out_readings != 1:
        raise budget.BudgetRefused("held_out_readings", "the budget must allow one protected reading per Candidate")
    admitted = {}
    candidates = set()
    configurations = set()
    returned = set()
    for commit, event in _history(directory):
        event_run = event["job_run"]
        event_identity = RUN.fullmatch(event_run)
        if event_identity[1] != repository:
            raise readings.RecordError("a reservation names a run outside this repository")
        if event["kind"] == "return":
            if event_run not in admitted or event_run in returned or not _verified_return(repository, commit, admitted[event_run], event_identity[2]):
                raise readings.RecordError("only the repository owner may merge a return for a completed, spent reservation")
            returned.add(event_run)
            continue
        if event_run in admitted:
            raise readings.RecordError("a GitHub run may carry only one reservation")
        if event["candidate"] in candidates or event["configuration_digest"] in configurations:
            raise budget.BudgetRefused("held_out_readings", "this Candidate has already reserved its held-out reading")
        if len(admitted) - len(returned) >= limits.held_out_total_readings:
            raise budget.BudgetRefused("held_out_total_readings", "the protected reading budget is spent")
        candidates.add(event["candidate"])
        configurations.add(event["configuration_digest"])
        admitted[event_run] = event
    reservation = admitted.get(run)
    if reservation is None:
        if candidate in candidates:
            raise budget.BudgetRefused("held_out_readings", "this Candidate has already reserved its held-out reading")
        if len(admitted) - len(returned) >= limits.held_out_total_readings:
            raise budget.BudgetRefused("held_out_total_readings", "the protected reading budget is spent")
        raise budget.BudgetRefused("reading_reservation", "this dispatch has no committed reservation")
    if run in returned or (reservation["candidate"], reservation["job_commit"], reservation["hypotrail_commit"]) != (candidate, job_commit, hypotrail_commit):
        raise budget.BudgetRefused("reading_reservation", "this dispatch does not match its committed reservation")
    return Permit(reservation["configuration_digest"], next(iter(admitted.values()))["hypotrail_commit"])


def verify_frozen_fixture(workload_dir: Path, candidate: str, first_commit: str) -> None:
    """Compare Git object names against the first reserved Hypotrail checkout before opening the adapter."""
    fixture = f"fixtures/answers-{candidate}.jsonl"
    prefix = _git(workload_dir, "rev-parse", "--show-prefix")
    if prefix.returncode:
        raise readings.RecordError("the Candidate is not in a Git checkout")
    object_path = prefix.stdout.decode("utf-8").rstrip("\n") + fixture
    current = _git(workload_dir, "rev-parse", "--verify", f"HEAD:{object_path}")
    if current.returncode or _git(workload_dir, "diff", "--quiet", "--no-ext-diff", "HEAD", "--", fixture).returncode:
        raise readings.RecordError("the Candidate fixture is missing from or differs from its frozen Git checkout")
    if _git(workload_dir, "cat-file", "-e", f"{first_commit}^{{commit}}").returncode:
        raise readings.RecordError("the first reserved Hypotrail checkout is unavailable")
    original = _git(workload_dir, "rev-parse", "--verify", f"{first_commit}:{object_path}")
    if original.returncode or original.stdout != current.stdout:
        raise budget.BudgetRefused("made_after_first_reading", "the frozen Candidate fixture did not exist at the first reservation")
