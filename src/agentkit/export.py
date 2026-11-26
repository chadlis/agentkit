"""Writes `answers.jsonl` and `population.json` for `hypotrail import agent` (`docs/design.md`, section 2)."""

import json
from collections import Counter
from pathlib import Path

from agentkit.adapters.scripted import ORIGIN, ScriptedAdapter
from agentkit.cases import read_cases

REPEATS = (0,)


def export(workload_dir: Path, partition: str, adapter: ScriptedAdapter, out: Path) -> Counter:
    """Runs `adapter` over every (Case, Repeat) of `partition` and writes one line per pair; returns the count of each observation status."""
    pairs = [(case, repeat) for case in read_cases(workload_dir, partition) for repeat in REPEATS]
    lines = []
    for case, repeat in pairs:
        answer = adapter.answer(case, repeat)
        line = {"case_id": case.case_id, "repeat": repeat}
        line |= answer.fields if answer is not None else {"observation_status": "missing"}
        line["origin"] = ORIGIN
        if adapter.settings is not None:
            line["settings"] = adapter.settings
        lines.append(line)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "answers.jsonl").open("w", encoding="utf-8", newline="\n") as answers:
        for line in lines:
            answers.write(json.dumps(line, ensure_ascii=False) + "\n")
    population = {"episodes": [{"case_id": case.case_id, "repeat": repeat} for case, repeat in pairs]}
    (out / "population.json").write_text(json.dumps(population, indent=2) + "\n", encoding="utf-8")
    return Counter(line["observation_status"] for line in lines)
