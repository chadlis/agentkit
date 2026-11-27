"""The readings record, `readings.jsonl` (`docs/design.md`, section 5): one line per held-out reading of an earlier evaluator job, committed through a pull request, holding the candidate's name, its Configuration digest, the job's commit and the reading's status, and nothing else. Offline; no model call."""

import hashlib
import json
from pathlib import Path

import pytest

from agentkit import readings

ROOT = Path(__file__).resolve().parents[1]
CONCISE = {"candidate": "concise", "configuration_digest": "c" * 64, "job_commit": "a" * 40, "status": "completed"}


def write_record(*lines) -> Path:
    path = Path("readings.jsonl")
    path.write_text("".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines), encoding="utf-8")
    return path


def test_the_committed_record_is_a_valid_readings_record():
    readings.load(ROOT / "readings.jsonl")


def test_a_record_of_well_formed_lines_is_read_in_order():
    failed = CONCISE | {"candidate": "verbose", "configuration_digest": "d" * 64, "status": "failed"}
    assert readings.load(write_record(CONCISE, failed)) == [CONCISE, failed]


@pytest.mark.parametrize(
    "line",
    [
        pytest.param(CONCISE | {"pairs": [["X01", 0]]}, id="another-field"),
        pytest.param({key: value for key, value in CONCISE.items() if key != "status"}, id="a-field-missing"),
        pytest.param(CONCISE | {"candidate": "a free text"}, id="candidate-not-a-fixture-name"),
        pytest.param(CONCISE | {"configuration_digest": "not a digest"}, id="digest-not-64-hex"),
        pytest.param(CONCISE | {"job_commit": "main"}, id="job-commit-not-40-hex"),
        pytest.param(CONCISE | {"status": "refused"}, id="status-not-spent"),
        pytest.param("not json", id="not-json"),
        pytest.param([CONCISE], id="not-an-object"),
    ],
)
def test_a_record_line_with_any_other_field_or_form_is_refused_as_invalid_input(line):
    with pytest.raises(readings.RecordError):
        readings.load(write_record(line))


def test_a_missing_record_is_invalid_input():
    with pytest.raises(readings.RecordError):
        readings.load(Path("no-readings.jsonl"))


def test_the_configuration_digest_is_the_sha256_of_the_settings_as_canonical_json_whatever_their_key_order():
    settings = {"configuration": "concise", "seed": 1}
    expected = hashlib.sha256(b'{"configuration":"concise","seed":1}').hexdigest()
    assert (readings.digest(settings), readings.digest({"seed": 1, "configuration": "concise"})) == (expected, expected)


def test_the_readings_of_a_candidate_are_the_lines_of_its_name_or_of_its_configuration_digest():
    other = CONCISE | {"candidate": "verbose", "configuration_digest": "d" * 64}
    same_settings = CONCISE | {"candidate": "renamed"}
    record = [CONCISE, other, same_settings]
    assert readings.of(record, "concise", "e" * 64) == [CONCISE]
    assert readings.of(record, "fresh", "c" * 64) == [CONCISE, same_settings]
    assert readings.of(record, "fresh", "e" * 64) == []


def test_a_reading_line_holds_the_four_fields_of_the_record_and_nothing_else():
    assert readings.line("concise", "c" * 64, "a" * 40, "failed") == {"candidate": "concise", "configuration_digest": "c" * 64, "job_commit": "a" * 40, "status": "failed"}
