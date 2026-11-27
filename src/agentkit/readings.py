"""The committed, metadata-only record of earlier evaluator jobs."""

import hashlib
import json
import os
import re
from pathlib import Path

from agentkit import budget, evaluator
from agentkit.conductor import CONFIGURATION

RECORD_FILE = Path("readings.jsonl")
LINE_FILE = "reading.jsonl"
KEYS = ("candidate", "configuration_digest", "job_commit", "job_run", "status")
STATUSES = ("completed", "failed")
DIGEST = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
JOB_RUN = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[1-9][0-9]*")


class RecordError(ValueError):
    """The readings record cannot be trusted as a budget input."""


def digest(settings) -> str:
    """The sha256 of the Configuration as canonical JSON."""
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _well_formed(item) -> bool:
    return (
        isinstance(item, dict) and set(item) == set(KEYS)
        and all(isinstance(item[key], str) for key in KEYS)
        and CONFIGURATION.fullmatch(item["candidate"]) is not None
        and DIGEST.fullmatch(item["configuration_digest"]) is not None
        and COMMIT.fullmatch(item["job_commit"]) is not None
        and JOB_RUN.fullmatch(item["job_run"]) is not None
        and item["status"] in STATUSES
    )


def load(path: Path = RECORD_FILE) -> list[dict]:
    """Read and validate the committed record. Missing or malformed input fails closed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise RecordError(f"cannot read {path}") from error
    items = []
    jobs = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise RecordError(f"{path}, line {number}: invalid JSON") from error
        if not _well_formed(item):
            raise RecordError(f"{path}, line {number}: invalid reading metadata")
        if item["job_run"] in jobs:
            raise RecordError(f"{path}, line {number}: duplicate job run")
        jobs.add(item["job_run"])
        items.append(item)
    return items


def check(recorded: list[dict], local: list[dict], candidate: str, settings, total: int) -> None:
    """Check the candidate and overall budget across the committed record and this checkout."""
    spent = [item for item in local if item["set"] == evaluator.HELD_OUT and item["status"] != "refused"]
    configuration = digest(settings)
    if any(item["candidate"] == candidate or item["configuration_digest"] == configuration for item in recorded) or any(
        item.get("candidate") == candidate or item["configuration"] == settings for item in spent
    ):
        raise budget.BudgetRefused("held_out_readings", "this Candidate has had its held-out reading")
    committed_jobs = {item["job_run"] for item in recorded}
    if len(recorded) + sum(item.get("job_run") not in committed_jobs for item in spent) >= total:
        raise budget.BudgetRefused("held_out_total_readings", f"the {total} held-out readings are spent")


def write(out: Path, candidate: str, configuration: str, job_commit: str, job_run: str, status: str) -> None:
    """Write the job's metadata separately from the aggregate report."""
    item = dict(zip(KEYS, (candidate, configuration, job_commit, job_run, status), strict=True))
    if not _well_formed(item):
        raise RecordError("the job cannot name its reading in the committed record")
    temporary = out / f".{LINE_FILE}.tmp"
    try:
        out.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, out / LINE_FILE)
    except OSError as error:
        raise RecordError("the reading metadata could not be saved") from error
    finally:
        temporary.unlink(missing_ok=True)
