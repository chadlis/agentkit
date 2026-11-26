"""The Cases of one Partition of a Workload. This module reads `workload.json` and the Cases file it names; it never opens the References file."""

import json
from dataclasses import dataclass
from pathlib import Path

from agentkit import evaluator

DEV = "dev"
PARTITIONS = (DEV, evaluator.HELD_OUT)


class WorkloadError(ValueError):
    """The Workload directory, its `workload.json` or a Cases file cannot be read as a Workload."""


@dataclass(frozen=True)
class Case:
    case_id: str
    question: str
    group: str
    family: str


def read_json_lines(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkloadError(f"{path}: {error}") from error
    if not all(isinstance(row, dict) for row in rows):
        raise WorkloadError(f"{path}: every line must be a JSON object")
    return rows


def read_cases(workload_dir: Path, partition: str) -> list[Case]:
    """The Cases of `partition`, in file order. The held-out Partition is refused outside the evaluator entry point."""
    if partition not in PARTITIONS:
        raise WorkloadError(f"no Partition {partition!r}: only {', '.join(PARTITIONS)}")
    if partition == evaluator.HELD_OUT and not evaluator.inside():
        raise evaluator.refusal()
    try:
        workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
        relative = workload["cases"][partition]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise WorkloadError(f"{workload_dir}: no {partition} Cases in workload.json ({error})") from error
    cases = []
    for row in read_json_lines(workload_dir / relative):
        try:
            cases.append(Case(case_id=row["case_id"], question=row["question"], group=row["group"], family=row["family"]))
        except KeyError as error:
            raise WorkloadError(f"{workload_dir / relative}: a Case has no {error}") from error
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise WorkloadError(f"{workload_dir / relative}: a case_id is repeated")
    return cases
