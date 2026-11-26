"""The Cases of one Partition of a Workload. This module reads `workload.json` and the Cases file it names; it never opens the References file."""

import json
from dataclasses import dataclass
from pathlib import Path

DEV = "dev"


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
    """The Cases of `partition`, in file order. Only `dev` is read: the held-out set belongs to the evaluator job."""
    if partition != DEV:
        raise WorkloadError(f"only the {DEV} Partition can be read here, not {partition!r}")
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
