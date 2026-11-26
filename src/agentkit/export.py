"""Writes `answers.jsonl` and `population.json` for `hypotrail import agent` (`docs/design.md`, section 2), from a closed run of the ledger."""

import json
from collections import Counter
from pathlib import Path

from agentkit import ledger
from agentkit.adapters import live, scripted

# The `origin` of a line, by the adapter that produced it.
ORIGINS = {scripted.NAME: scripted.ORIGIN, live.NAME: live.ORIGIN}


def export(run_id: str, out: Path, ledger_dir: Path = ledger.LEDGER_DIR) -> Counter:
    """Writes one line per (Case, Repeat) of the closed run `run_id`; returns the count of each observation status.

    A pair a stopped run never reached is `missing`, with the reason the run stopped in its `metadata`."""
    record, answers = ledger.closed_run(ledger_dir, run_id)
    pairs = [(case_id, repeat) for case_id, repeat in record["pairs"]]
    lines = []
    for case_id, repeat in pairs:
        fields = answers.get((case_id, repeat))
        line = {"case_id": case_id, "repeat": repeat}
        if fields is not None:
            line |= fields
        elif (case_id, repeat) not in answers and record.get("reason") is not None:
            line |= {"observation_status": "missing", "metadata": {"reason": record["reason"]}}
        else:
            line["observation_status"] = "missing"
        if record["adapter"] in ORIGINS:
            line["origin"] = ORIGINS[record["adapter"]]
        if record["configuration"] is not None:
            line["settings"] = record["configuration"]
        lines.append(line)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "answers.jsonl").open("w", encoding="utf-8", newline="\n") as file:
        for line in lines:
            file.write(json.dumps(line, ensure_ascii=False) + "\n")
    population = {"episodes": [{"case_id": case_id, "repeat": repeat} for case_id, repeat in pairs]}
    (out / "population.json").write_text(json.dumps(population, indent=2) + "\n", encoding="utf-8")
    return Counter(line["observation_status"] for line in lines)
