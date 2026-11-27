"""The run ledger, `ledger/runs.jsonl` of the working directory: one record per harness run, and the answers each run recorded."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

LEDGER_DIR = Path("ledger")
STATUSES = ("completed", "failed", "refused", "running")


class LedgerError(ValueError):
    """The ledger cannot be read, or holds no record the request can use."""


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_file(ledger_dir: Path) -> Path:
    return ledger_dir / "runs.jsonl"


def answers_file(ledger_dir: Path, run_id: str) -> Path:
    return ledger_dir / "answers" / f"{run_id}.jsonl"


def read_records(ledger_dir: Path = LEDGER_DIR) -> list[dict]:
    """The records in the order the runs started; none when the ledger does not exist. Reads only."""
    path = runs_file(ledger_dir)
    if not path.is_file():
        return []
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LedgerError(f"{path}: {error}") from error


def write_records(ledger_dir: Path, records: list[dict]) -> None:
    """Replaces the ledger with `records`, through a temporary file, so that a crash never leaves it half written."""
    path = runs_file(ledger_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def fail_stale_runs(records: list[dict]) -> None:
    """Marks every record still `running` as `failed`: the run it counts crashed, since runs do not overlap."""
    for record in records:
        if record["status"] == "running":
            record["status"] = "failed"
            record["end_time"] = now()


def open_run(ledger_dir: Path, *, partition: str, adapter: str, configuration: dict | None, pairs: list[tuple[str, int]]) -> str:
    """Marks every record still `running` as `failed`, then appends a `running` record for the new run; returns its run id."""
    records = read_records(ledger_dir)
    fail_stale_runs(records)
    run_id = f"r-{len(records) + 1:04d}"
    records.append({
        "run_id": run_id,
        "set": partition,
        "adapter": adapter,
        "configuration": configuration,
        "pairs": [[case_id, repeat] for case_id, repeat in pairs],
        "start_time": now(),
        "status": "running",
        "end_time": None,
    })
    write_records(ledger_dir, records)
    return run_id


def refuse_run(ledger_dir: Path, *, partition: str, adapter: str, configuration: dict | None, reason: str | None = None) -> str:
    """Marks every record still `running` as `failed`, as `open_run` does, then appends a `refused` record, closed when written, with no pair, and the `reason` when one is given. Returns its run id."""
    records = read_records(ledger_dir)
    fail_stale_runs(records)
    run_id = f"r-{len(records) + 1:04d}"
    time = now()
    records.append({
        "run_id": run_id,
        "set": partition,
        "adapter": adapter,
        "configuration": configuration,
        "pairs": [],
        "start_time": time,
        "status": "refused",
        "end_time": time,
    } | ({} if reason is None else {"reason": reason}))
    write_records(ledger_dir, records)
    return run_id


def close_run(ledger_dir: Path, run_id: str, status: str = "completed", reason: str | None = None, stop: str | None = None) -> None:
    """Closes the run; a run the harness stops before its last pair is `failed` with the `reason` it stopped for, and a run that fired a stop rule names its `stop`."""
    records = read_records(ledger_dir)
    record = next(item for item in records if item["run_id"] == run_id)
    record["status"] = status
    record["end_time"] = now()
    if reason is not None:
        record["reason"] = reason
    if stop is not None:
        record["stop"] = stop
    write_records(ledger_dir, records)


def record_answer(ledger_dir: Path, run_id: str, case_id: str, repeat: int, fields: dict | None) -> None:
    """Appends what the adapter returned for one pair; `None` when it returned no answer."""
    path = answers_file(ledger_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps({"case_id": case_id, "repeat": repeat, "answer": fields}, ensure_ascii=False) + "\n")


def closed_run(ledger_dir: Path, run_id: str) -> tuple[dict, dict[tuple[str, int], dict | None]]:
    """The record `run_id` and the answers it recorded, by pair: a `completed` run, or a `failed` one the harness closed with a reason. Any other run is refused."""
    record = next((item for item in read_records(ledger_dir) if item["run_id"] == run_id), None)
    if record is None:
        raise LedgerError(f"no run {run_id!r} in {runs_file(ledger_dir)}")
    stopped = record["status"] == "failed" and record.get("reason") is not None
    if record["status"] != "completed" and not stopped:
        raise LedgerError(f"run {run_id} is {record['status']}, not completed nor stopped by the harness with a reason: only a closed run is exported")
    path = answers_file(ledger_dir, run_id)
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LedgerError(f"{path}: {error}") from error
    return record, {(row["case_id"], row["repeat"]): row["answer"] for row in rows}


def counts(records: list[dict]) -> dict[str, dict[str, int]]:
    """The number of runs per set and per status."""
    table: dict[str, dict[str, int]] = {}
    for record in records:
        row = table.setdefault(record["set"], dict.fromkeys(STATUSES, 0))
        row[record["status"]] = row.get(record["status"], 0) + 1
    return table
