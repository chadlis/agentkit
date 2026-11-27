"""The readings record, `readings.jsonl` of the working directory (`docs/design.md`, section 5): one line per held-out reading of an earlier evaluator job, so that the count of readings holds across job runs, whose runners start with an empty ledger.

The job cannot write it: it holds `contents: read` and no secret. It publishes its reading's line, `reading.jsonl`, and a person appends that line to the record through a pull request, as a limit of `budget.json` is raised. A line holds the candidate's name, its Configuration digest, the job's commit and the reading's status, and nothing else: no held-out text, no per-Case result."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

from agentkit.conductor import CONFIGURATION

RECORD_FILE = Path("readings.jsonl")
# The line a reading publishes, for a person to append to the record.
LINE_FILE = "reading.jsonl"
KEYS = ("candidate", "configuration_digest", "job_commit", "status")
# A reading that is counted is spent, whether it completed or failed; a refused one is never a reading.
STATUSES = ("completed", "failed")
MADE_AFTER = "made_after_first_reading"
DIGEST = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")


class RecordError(ValueError):
    """`readings.jsonl` is missing or malformed, or the checkouts cannot date a candidate against it: invalid input."""


def digest(settings) -> str:
    """The Configuration digest: the sha256 of the run's settings as canonical JSON."""
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def line(candidate: str, configuration_digest: str, job_commit: str, status: str) -> dict:
    return {"candidate": candidate, "configuration_digest": configuration_digest, "job_commit": job_commit, "status": status}


def _well_formed(item) -> bool:
    return (
        isinstance(item, dict) and set(item) == set(KEYS)
        and isinstance(item["candidate"], str) and CONFIGURATION.fullmatch(item["candidate"]) is not None
        and isinstance(item["configuration_digest"], str) and DIGEST.fullmatch(item["configuration_digest"]) is not None
        and isinstance(item["job_commit"], str) and COMMIT.fullmatch(item["job_commit"]) is not None
        and item["status"] in STATUSES
    )


def load(path: Path = RECORD_FILE) -> list[dict]:
    """The record's lines, in order. A missing file is invalid input, so that deleting the record never reads as no reading."""
    try:
        texts = [text for text in path.read_text(encoding="utf-8").splitlines() if text.strip()]
        items = [json.loads(text) for text in texts]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RecordError(f"{path}: {error}") from error
    for number, item in enumerate(items, start=1):
        if not _well_formed(item):
            raise RecordError(f"{path}, line {number}: a reading is an object of exactly {', '.join(KEYS)}: a fixture name, a 64-hex digest, a 40-hex commit and {' or '.join(STATUSES)}")
    return items


def of(record: list[dict], candidate: str, configuration_digest: str) -> list[dict]:
    """The readings of the candidate: the lines of its name or of its Configuration digest."""
    return [item for item in record if item["candidate"] == candidate or item["configuration_digest"] == configuration_digest]


def _git(directory: Path, *args: str) -> str | None:
    done = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def _first_reading_time(record: list[dict], kit_dir: Path) -> int:
    """The commit time of the earliest job commit of the record, in the kit's checkout."""
    times = []
    for item in record:
        time = _git(kit_dir, "show", "--no-patch", "--format=%ct", f"{item['job_commit']}^{{commit}}")
        if time is None or not time.isdigit():
            raise RecordError(f"the kit's checkout holds no commit {item['job_commit']} of {RECORD_FILE}: the job checks the kit out with its whole history, to date its readings")
        times.append(int(time))
    return min(times)


def _made_time(workload_dir: Path, candidate: str) -> int:
    """The commit time of the last commit that changed the candidate's fixture, in the Hypotrail checkout that holds the Workload."""
    fixture = f"fixtures/answers-{candidate}.jsonl"
    if _git(workload_dir, "rev-parse", "--is-shallow-repository") != "false":
        raise RecordError(f"the Hypotrail checkout of {workload_dir} is shallow: the job checks it out with its whole history, to date {fixture}")
    if _git(workload_dir, "status", "--porcelain", "--", fixture) != "":
        raise RecordError(f"{fixture} differs from its last commit: only a committed fixture is dated")
    time = _git(workload_dir, "log", "-1", "--format=%ct", "--", fixture)
    if time is None or not time.isdigit():
        raise RecordError(f"no commit of the Hypotrail checkout dates {fixture}")
    return int(time)


def made_after(record: list[dict], workload_dir: Path, candidate: str, kit_dir: Path = Path(".")) -> bool:
    """True when the record holds a reading and the candidate's fixture was last committed after the first one's job commit.

    The record holds no time. A reading runs after its job commit, so that commit's time is the earliest the reading can have been: a fixture committed later is not proven to be older than the reading. Both times are committer times, which a rebase moves later."""
    if not record:
        return False
    return _made_time(workload_dir, candidate) > _first_reading_time(record, kit_dir)
