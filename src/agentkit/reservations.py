"""The evaluator's durable, metadata-only spending record on Agentkit main."""

import json
import re
import stat
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
    earlier_candidates: tuple[str, ...]


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", "-C", str(directory), *args], capture_output=True)
    except OSError:
        raise readings.RecordError("cannot read the committed reservation history") from None


def _history(directory: Path, head: str):
    """Read only the first-parent commits of the Agentkit main checkout, never its worktree file."""
    depth = _git(directory, "rev-parse", "--is-shallow-repository")
    if depth.returncode or depth.stdout != b"false\n":
        raise readings.RecordError("the complete committed reservation history is required")
    log = _git(directory, "log", "--first-parent", "--full-history", "--reverse", "--format=%H", head, "--", RECORD)
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


def _committed_budget(directory: Path, commit: str) -> budget.Budget:
    location = f"{commit}:{budget.BUDGET_FILE}"
    snapshot = _git(directory, "show", location)
    if snapshot.returncode:
        raise budget.BudgetError("the committed reservation budget is unavailable")
    return budget.parse(snapshot.stdout, location)


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
    """Separate a disproved return from unavailable evidence, which closes the gate."""
    route = f"/repos/{repository}"
    try:
        owner = _github_json(route)["owner"]["id"]
        associated = _github_json(f"{route}/commits/{commit}/pulls")
        number, = [pull["number"] for pull in associated if pull["merge_commit_sha"] == commit]
        if type(number) is not int or number <= 0 or type(owner) is not int or owner <= 0:
            raise ValueError
        pull = _github_json(f"{route}/pulls/{number}")
        if type(pull["merged"]) is not bool:
            raise ValueError
        if not pull["merged"]:
            return False
        merger = pull["merged_by"]["id"]
        merge_sha = pull["merge_commit_sha"]
        base_ref, base_repository = pull["base"]["ref"], pull["base"]["repo"]["full_name"]
        if type(merger) is not int or merger <= 0 or not readings.COMMIT.fullmatch(merge_sha):
            raise ValueError
        if not isinstance(base_ref, str) or not isinstance(base_repository, str):
            raise ValueError
        if merge_sha != commit or merger != owner or base_ref != "main" or base_repository != repository:
            return False
        run = _github_json(f"{route}/actions/runs/{run_number}/attempts/1")
        if run["status"] in ("queued", "in_progress", "waiting", "requested", "pending"):
            return False
        if run["status"] != "completed" or type(run["id"]) is not int or type(run["run_attempt"]) is not int:
            raise ValueError
        if not readings.COMMIT.fullmatch(run["head_sha"]):
            raise ValueError
        if run["id"] != int(run_number) or run["run_attempt"] != 1 or run["head_sha"] != reservation["job_commit"]:
            return False
        completed_at = datetime.fromisoformat(run["updated_at"])
        merged_at = datetime.fromisoformat(pull["merged_at"])
        if completed_at.tzinfo is None or merged_at.tzinfo is None:
            raise ValueError
        return completed_at < merged_at
    except readings.RecordError:
        raise
    except (KeyError, TypeError, ValueError):
        raise readings.RecordError("GitHub did not provide complete evidence for the reservation return") from None


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
    limits.protected_total()
    snapshot = _git(directory, "rev-parse", "--verify", "HEAD")
    if snapshot.returncode:
        raise readings.RecordError("the committed reservation snapshot is unavailable")
    head = snapshot.stdout.decode("ascii").strip()
    total_readings = _committed_budget(directory, head).protected_total()
    admitted = {}
    refused = {}
    candidates = set()
    configurations = set()
    returned = set()
    for commit, event in _history(directory, head):
        event_run = event["job_run"]
        event_identity = RUN.fullmatch(event_run)
        if event_identity[1] != repository:
            raise readings.RecordError("a reservation names a run outside this repository")
        if event["kind"] == "return":
            if event_run in admitted and event_run not in returned and _verified_return(repository, commit, admitted[event_run], event_identity[2]):
                returned.add(event_run)
            continue
        if event_run in admitted or event_run in refused:
            continue
        if event["candidate"] in candidates or event["configuration_digest"] in configurations:
            refused[event_run] = ("held_out_readings", "this Candidate has already reserved its held-out reading")
            continue
        policy = _committed_budget(directory, commit)
        if policy.held_out_total_readings is None:
            refused[event_run] = ("reading_reservation", "this dispatch was reserved without an explicit committed total")
            continue
        if policy.held_out_readings != 1:
            refused[event_run] = ("held_out_readings", "the committed budget did not allow one protected reading per Candidate")
            continue
        if len(admitted) - len(returned) >= policy.held_out_total_readings:
            refused[event_run] = (budget.TOTAL_READINGS, "the protected reading budget is spent")
            continue
        candidates.add(event["candidate"])
        configurations.add(event["configuration_digest"])
        admitted[event_run] = event
    reservation = admitted.get(run)
    if reservation is None:
        if run in refused:
            raise budget.BudgetRefused(*refused[run])
        if candidate in candidates:
            raise budget.BudgetRefused("held_out_readings", "this Candidate has already reserved its held-out reading")
        if len(admitted) - len(returned) >= total_readings:
            raise budget.BudgetRefused(budget.TOTAL_READINGS, "the protected reading budget is spent")
        raise budget.BudgetRefused("reading_reservation", "this dispatch has no committed reservation")
    if run in returned or (reservation["candidate"], reservation["job_commit"], reservation["hypotrail_commit"]) != (candidate, job_commit, hypotrail_commit):
        raise budget.BudgetRefused("reading_reservation", "this dispatch does not match its committed reservation")
    earlier_candidates = []
    for admitted_run, event in admitted.items():
        if admitted_run == run:
            break
        earlier_candidates.append(event["candidate"])
    return Permit(reservation["configuration_digest"], next(iter(admitted.values()))["hypotrail_commit"], tuple(earlier_candidates))


def _regular_git_blob(workload_dir: Path, revision: str, path: str) -> bytes | None:
    entry = _git(workload_dir, "ls-tree", "--full-tree", "-z", revision, "--", path)
    if entry.returncode:
        raise readings.RecordError("the frozen Candidate Git object is unavailable")
    if not entry.stdout:
        return None
    try:
        record, end = entry.stdout.split(b"\0")
        description, listed_path = record.split(b"\t", 1)
        mode, object_type, digest = description.split()
        if end or listed_path != path.encode("utf-8"):
            raise ValueError
    except ValueError:
        raise readings.RecordError("the frozen Candidate Git object is invalid") from None
    return digest if mode in (b"100644", b"100755") and object_type == b"blob" else None


def verify_frozen_fixture(workload_dir: Path, candidate: str, first_commit: str, earlier_candidates: tuple[str, ...] = ()) -> None:
    """Compare Git object names against the first reserved Hypotrail checkout before opening the adapter."""
    fixture = f"fixtures/answers-{candidate}.jsonl"
    prefix = _git(workload_dir, "rev-parse", "--show-prefix")
    if prefix.returncode:
        raise readings.RecordError("the Candidate is not in a Git checkout")
    object_prefix = prefix.stdout.decode("utf-8").rstrip("\n")
    object_path = object_prefix + fixture
    current = _regular_git_blob(workload_dir, "HEAD", object_path)
    if current is None:
        raise readings.RecordError("the Candidate fixture must be a regular file in its frozen Git checkout")
    if _git(workload_dir, "cat-file", "-e", f"{first_commit}^{{commit}}").returncode:
        raise readings.RecordError("the first reserved Hypotrail checkout is unavailable")
    original = _regular_git_blob(workload_dir, first_commit, object_path)
    if original != current:
        raise budget.BudgetRefused("made_after_first_reading", "the frozen Candidate fixture did not exist at the first reservation")
    for earlier in earlier_candidates:
        prior = _regular_git_blob(workload_dir, first_commit, f"{object_prefix}fixtures/answers-{earlier}.jsonl")
        if prior == current:
            raise budget.BudgetRefused("held_out_readings", "this fixture is identical to an earlier reserved Candidate")
    path = workload_dir / fixture
    try:
        info = path.lstat()
        folder = path.parent.lstat()
    except OSError:
        raise readings.RecordError("the evaluator requires a regular unlinked Candidate fixture") from None
    if not stat.S_ISDIR(folder.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise readings.RecordError("the evaluator requires a regular unlinked Candidate fixture")
    if _git(workload_dir, "diff", "--quiet", "--no-ext-diff", "HEAD", "--", fixture).returncode:
        raise readings.RecordError("the Candidate fixture is missing from or differs from its frozen Git checkout")
