"""`agentkit export` with the scripted adapter on the dev set of the twin, checked against `contracts/agent-answers-1/`. Offline; no model call."""

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import jsonschema

from agentkit.cli import main

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "agent-answers-1"
LINE_KEYS = {"case_id", "repeat", "observation_status", "raw_answer", "outcome", "origin", "producer", "usage", "metadata", "settings"}


def export(workload_dir: Path, out: Path, fixture: str = "concise") -> tuple[list[dict], dict]:
    code = main(["export", "--workload-dir", str(workload_dir), "--set", "dev", "--adapter", "scripted", "--fixture", fixture, "--out", str(out)])
    assert code == 0
    answers = out / "answers.jsonl"
    lines = [json.loads(text) for text in answers.read_text(encoding="utf-8").splitlines()] if answers.is_file() else []
    population_file = out / "population.json"
    population = json.loads(population_file.read_text(encoding="utf-8")) if population_file.is_file() else {"episodes": []}
    return lines, population


def dev_case_ids(workload_dir: Path) -> list[str]:
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    cases = (workload_dir / workload["cases"]["dev"]).read_text(encoding="utf-8").splitlines()
    return [json.loads(text)["case_id"] for text in cases if text.strip()]


def test_the_concise_dev_export_has_one_line_per_dev_case_at_repeat_0_and_a_population_of_the_same_pairs(workload_dir, tmp_path):
    lines, population = export(workload_dir, tmp_path)
    expected = [{"case_id": case_id, "repeat": 0} for case_id in dev_case_ids(workload_dir)]
    assert len(expected) == 30
    assert [{"case_id": line["case_id"], "repeat": line["repeat"]} for line in lines] == expected
    assert population == {"episodes": expected}


def test_every_line_holds_only_import_agent_keys_and_the_same_settings_object(workload_dir, tmp_path):
    lines, _ = export(workload_dir, tmp_path)
    assert lines
    assert [set(line) - LINE_KEYS for line in lines] == [set() for _ in lines]
    settings = [line.get("settings") for line in lines]
    assert settings[0]
    assert settings == [settings[0]] * len(settings)


def test_each_fixture_export_validates_against_the_agent_answers_1_schemas(workload_dir, tmp_path):
    line_schema = jsonschema.Draft202012Validator(json.loads((CONTRACT / "line.schema.json").read_text(encoding="utf-8")))
    population_schema = jsonschema.Draft202012Validator(json.loads((CONTRACT / "population.schema.json").read_text(encoding="utf-8")))
    for fixture in ("concise", "verbose"):
        lines, population = export(workload_dir, tmp_path / fixture, fixture)
        assert len(lines) == 30
        assert [[error.message for error in line_schema.iter_errors(line)] for line in lines] == [[] for _ in lines]
        assert [error.message for error in population_schema.iter_errors(population)] == []


_watched: list[tuple[str, list[str]]] = []


def _record_open(event: str, args: tuple) -> None:
    if event == "open" and _watched and isinstance(args[0], (str, bytes, os.PathLike)):
        opened = os.path.realpath(os.fsdecode(args[0]))
        for path, seen in _watched:
            if opened == path:
                seen.append(opened)


sys.addaudithook(_record_open)


@contextmanager
def references_guard(path: Path):
    """Records every open of `path` inside the block, whatever opens it."""
    seen: list[str] = []
    _watched.append((os.path.realpath(path), seen))
    try:
        yield seen
    finally:
        _watched.pop()


def test_the_guard_sees_an_open_of_the_references_file_and_the_exporter_never_opens_it(workload_dir, tmp_path):
    workload = json.loads((workload_dir / "workload.json").read_text(encoding="utf-8"))
    references = workload_dir / workload["references"]
    with references_guard(references) as seen:
        references.open("rb").close()
    assert seen == [os.path.realpath(references)]
    with references_guard(references) as seen:
        for fixture in ("concise", "verbose"):
            lines, _ = export(workload_dir, tmp_path / fixture, fixture)
            assert len(lines) == 30
    assert seen == []
