"""Write the evaluator outcome using only publishable reading metadata."""

import hashlib
import json
import os
import re
from pathlib import Path

from agentkit.conductor import CONFIGURATION

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
