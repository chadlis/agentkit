"""The contracts under `contracts/` (D-172 on chadlis/hypotrail#75): each schema is valid, accepts its examples and refuses each refused case; each copy matches its line in `contracts.lock.json`. Offline; no model call."""

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from contract_formats import FORMATS

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
SCHEMAS = sorted(CONTRACTS.glob("*/*.schema.json"))
OWNED = ("kit-decision-1", "kit-aggregate-1", "kit-status-1")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines() if text.strip()]


def validator(directory, name):
    return jsonschema.Draft202012Validator(load(CONTRACTS / directory / name), format_checker=FORMATS)


def directory_digest(directory: Path) -> str:
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    manifest = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(directory).as_posix()}\n" for path in files)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("path", SCHEMAS, ids=[f"{p.parent.name}/{p.name}" for p in SCHEMAS])
def test_each_schema_is_a_valid_draft_2020_12_schema_whose_id_names_its_directory(path):
    schema = load(path)
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith(f"/contracts/{path.parent.name}/{path.name}")


def test_the_decision_log_examples_are_accepted_and_each_refused_line_is_refused():
    lines_validator = validator("kit-decision-1", "line.schema.json")
    log = lines(CONTRACTS / "kit-decision-1" / "examples" / "log.jsonl")
    assert [list(lines_validator.iter_errors(line)) for line in log] == [[] for _ in log]
    for case in lines(CONTRACTS / "kit-decision-1" / "examples" / "refused.jsonl"):
        assert list(lines_validator.iter_errors(case["line"])), case["case"]


@pytest.mark.parametrize("timestamp", [
    "2026-02-30T12:34:56Z",
    "2026-11-27T12:34:56",
    "2026-11-27T12:34:56+01:99",
    "2026-11-27 12:34:56Z",
])
def test_the_decision_line_refuses_an_invalid_timestamp(timestamp):
    checker = validator("kit-decision-1", "line.schema.json")
    example = lines(CONTRACTS / "kit-decision-1" / "examples" / "log.jsonl")[0]
    [error] = checker.iter_errors({**example, "time": timestamp})
    assert error.validator == "format"
    assert list(error.path) == ["time"]


def test_the_citation_examples_are_accepted_name_a_line_of_the_log_with_its_mark_and_the_refused_ones_are_refused():
    citation = validator("kit-decision-1", "citation.schema.json")
    examples = CONTRACTS / "kit-decision-1" / "examples"
    marks = {line["id"]: line["mark"] for line in lines(examples / "log.jsonl")}
    for text in (examples / "citations.txt").read_text(encoding="utf-8").splitlines():
        assert not list(citation.iter_errors(text)), text
        cited = text.split("#", 1)[1].split(";")
        assert marks[cited[0]] == cited[1].removeprefix("mark=")
    for text in (examples / "refused-citations.txt").read_text(encoding="utf-8").splitlines():
        assert list(citation.iter_errors(text)), text


@pytest.mark.parametrize(("mark", "level", "accepted"), [
    ("HUMAN", "human_unverified", True),
    ("HUMAN", "relayed", True),
    ("HUMAN", "pr", True),
    ("RELAYED", "relayed", True),
    ("PROXY", "none", True),
    ("RELAYED", "human_unverified", False),
    ("PROXY", "human_unverified", False),
])
def test_the_citation_and_status_schemas_accept_the_new_human_level_and_existing_levels(mark, level, accepted):
    citation = f"kit:chadlis/mlkit@{'0' * 40}:decisions/log.jsonl#K-0001;mark={mark};level={level}"
    status = load(CONTRACTS / "kit-status-1" / "examples" / "status.json")
    status["decisions"] = [{**status["decisions"][0], "mark": mark, "level": level}]

    valid_citation = validator("kit-decision-1", "citation.schema.json").is_valid(citation)
    valid_status = validator("kit-status-1", "status.schema.json").is_valid(status)
    assert (valid_citation, valid_status) == (accepted, accepted)


@pytest.mark.parametrize(("directory", "schema", "accepted"), [
    ("kit-aggregate-1", "aggregate.schema.json", ["aggregate.json", "aggregate-decided.json"]),
    ("kit-status-1", "status.schema.json", ["status.json"]),
])
def test_the_report_and_status_examples_are_accepted_and_each_refused_document_is_refused(directory, schema, accepted):
    checker = validator(directory, schema)
    for name in accepted:
        assert not list(checker.iter_errors(load(CONTRACTS / directory / "examples" / name))), name
    refused = lines(CONTRACTS / directory / "examples" / "refused.jsonl")
    assert refused
    for case in refused:
        assert list(checker.iter_errors(case["document"])), case["case"]


def test_the_copied_agent_answers_examples_are_accepted_and_its_refused_cases_refused():
    line = validator("agent-answers-1", "line.schema.json")
    examples = CONTRACTS / "agent-answers-1" / "examples"
    assert all(not list(line.iter_errors(entry)) for entry in lines(examples / "answers.jsonl"))
    for case in sorted(path for path in (examples / "refused").iterdir() if path.is_dir()):
        assert any(list(line.iter_errors(entry)) for entry in lines(case / "answers.jsonl")), case.name


def test_each_copy_matches_its_line_in_the_lock_and_every_contract_directory_is_owned_or_locked():
    lock = load(ROOT / "contracts.lock.json")["contracts"]
    for entry in lock:
        assert set(entry) == {"name", "version", "owner", "owner_commit", "sha256"}
        assert len(entry["owner_commit"]) == 40 and entry["owner"] != "chadlis/agentkit"
        directory = CONTRACTS / entry["name"].replace("/", "-")
        assert entry["version"] == int(entry["name"].rsplit("/", 1)[1])
        assert directory_digest(directory) == entry["sha256"], f"{directory.name} differs from its owner's copy at {entry['owner_commit']}"
    locked = {entry["name"].replace("/", "-") for entry in lock}
    assert {path.name for path in CONTRACTS.iterdir() if path.is_dir()} == locked | set(OWNED)
