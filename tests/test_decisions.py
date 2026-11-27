"""The decision log (`decisions/log.jsonl`, `kit-decision/1`), its writer `agentkit decide`, `agentkit cite` (`kit-citation/1`) and what `agentkit status` lists of them. Offline; no model call.

The words files here are written for the tests and are no person's words."""

import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
from contract_formats import FORMATS

from agentkit import decisions
from agentkit.cli import main

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "kit-decision-1"
LINE = jsonschema.Draft202012Validator(
    json.loads((CONTRACT / "line.schema.json").read_text(encoding="utf-8")), format_checker=FORMATS
)
CITATION = jsonschema.Draft202012Validator(json.loads((CONTRACT / "citation.schema.json").read_text(encoding="utf-8")))
PR = "https://github.com/chadlis/agentkit/pull/41@0123456789abcdef0123456789abcdef01234567"
RULE = "keep-higher-dev-score@1"
WORDS = "Example words written for this test, not a person's:\n  resume, the failures were a fixture error.  \n\n"


@pytest.fixture
def kit(tmp_path) -> dict:
    """A working directory with one completed run in its ledger and one stop file, as the harness writes them, and a words file."""
    (tmp_path / "ledger").mkdir()
    (tmp_path / "ledger" / "runs.jsonl").write_text(json.dumps({"run_id": "r-0001", "set": "dev", "status": "completed"}) + "\n", encoding="utf-8")
    (tmp_path / "stops").mkdir()
    (tmp_path / "stops" / "STOP-r-0002.json").write_text(json.dumps({"id": "STOP-r-0002", "rule": "failed-share@1", "run": "r-0002"}), encoding="utf-8")
    words = tmp_path / "words.txt"
    words.write_text(WORDS, encoding="utf-8")
    return {"root": tmp_path, "words": words}


def log(kit) -> list[dict]:
    path = kit["root"] / "decisions" / "log.jsonl"
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def decide(*args) -> int:
    return main(["decide", "--question", "Resume after stop STOP-r-0002?", "--option", "resume", *map(str, args)])


def proxy(*extra) -> int:
    return decide("--mark", "proxy", "--rule", RULE, "--run", "r-0001", *extra)


def human(kit, *extra) -> int:
    return decide("--mark", "human", "--words-file", kit["words"], "--stop", "STOP-r-0002", *extra)


def agentkit(*args, env: dict | None = None) -> subprocess.CompletedProcess:
    """The `agentkit` command in its own process, in the working directory of the test."""
    return subprocess.run([sys.executable, "-m", "agentkit.cli", *map(str, args)], capture_output=True, text=True, env={**os.environ, **(env or {})})


def git(root, *args) -> str:
    done = subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_a_proxy_decision_without_a_rule_is_refused_and_with_a_rule_the_line_holds_it(kit):
    assert decide("--mark", "proxy", "--run", "r-0001") == 2
    assert proxy("--words-file", kit["words"]) == 2
    assert log(kit) == []
    assert proxy() == 0
    [line] = log(kit)
    assert (line["mark"], line["rule"], line["source"], line["by"]) == ("PROXY", RULE, {"run": "r-0001"}, "cli")
    assert "words" not in line


def test_a_human_decision_without_words_is_refused_and_with_a_words_file_the_line_holds_the_words_whole(kit):
    assert decide("--mark", "human", "--stop", "STOP-r-0002") == 2
    empty = kit["root"] / "empty.txt"
    empty.write_text(" \n", encoding="utf-8")
    assert decide("--mark", "human", "--words-file", empty, "--stop", "STOP-r-0002") == 2
    assert human(kit, "--rule", RULE) == 2
    assert log(kit) == []
    assert human(kit) == 0
    [line] = log(kit)
    assert (line["mark"], line["words"], line["by"]) == ("HUMAN", WORDS, "cli")
    assert "rule" not in line


def test_the_conductor_s_process_cannot_write_human_and_its_proxy_line_names_it(kit):
    common = ["--question", "Resume after stop STOP-r-0002?", "--option", "resume"]
    conductor = {decisions.PROCESS_VARIABLE: "conductor"}
    refused = agentkit("decide", "--mark", "human", "--words-file", kit["words"], "--stop", "STOP-r-0002", *common, env=conductor)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert log(kit) == []
    with pytest.raises(decisions.DecisionError):
        decisions.append(question="q", option="o", mark="HUMAN", source={"stop": "STOP-r-0002"}, by="conductor", words_file=kit["words"])
    assert log(kit) == []
    written = agentkit("decide", "--mark", "proxy", "--rule", RULE, "--run", "r-0001", *common, env=conductor)
    assert written.returncode == 0, written.stdout + written.stderr
    assert [(line["mark"], line["by"]) for line in log(kit)] == [("PROXY", "conductor")]


def test_the_evaluator_entry_point_marks_its_process_so_that_neither_it_nor_a_child_writes_human(kit, monkeypatch):
    from agentkit import evaluator
    from test_held_out import evaluator_job

    evaluator_job(monkeypatch)
    with evaluator.entry_point():
        assert human(kit) == 2
        assert agentkit("decide", "--mark", "human", "--words-file", kit["words"], "--stop", "STOP-r-0002", "--question", "q", "--option", "o").returncode == 2
    assert human(kit) == 2
    assert log(kit) == []


def test_a_decision_cites_a_run_of_the_ledger_a_stop_file_or_a_pull_request_in_its_form(kit):
    assert decide("--mark", "proxy", "--rule", RULE) == 2
    assert decide("--mark", "proxy", "--rule", RULE, "--run", "r-0009") == 2
    assert decide("--mark", "human", "--words-file", kit["words"], "--stop", "STOP-r-0009") == 2
    assert decide("--mark", "human", "--words-file", kit["words"], "--pull-request", "https://github.com/chadlis/agentkit/pull/41") == 2
    assert decide("--mark", "proxy", "--rule", "Keep Higher", "--run", "r-0001") == 2
    assert log(kit) == []


def test_every_line_decide_writes_and_every_committed_line_validate_against_kit_decision_1_and_store_no_level(kit):
    assert proxy() == 0
    assert human(kit, "--pull-request", PR) == 0
    assert human(kit) == 0
    written = log(kit)
    assert [(line["id"], line["mark"]) for line in written] == [("K-0001", "PROXY"), ("K-0002", "HUMAN"), ("K-0003", "HUMAN")]
    committed = [json.loads(text) for text in (ROOT / "decisions" / "log.jsonl").read_text(encoding="utf-8").splitlines() if text.strip()]
    assert [[error.message for error in LINE.iter_errors(line)] for line in written + committed] == [[] for _ in written + committed]
    assert all("level" not in line for line in written + committed)


@pytest.mark.parametrize(("mark", "source", "level"), [
    ("HUMAN", {"pull_request": PR}, "pr"),
    ("HUMAN", {"stop": "STOP-r-0002", "pull_request": PR}, "pr"),
    ("HUMAN", {"pull_request": "https://github.com/chadlis/agentkit/pull/41"}, "relayed"),
    ("HUMAN", {"pull_request": PR + "0"}, "relayed"),
    ("HUMAN", {"stop": "STOP-r-0002"}, "relayed"),
    ("PROXY", {"pull_request": PR}, "none"),
    ("PROXY", {"run": "r-0001"}, "none"),
])
def test_the_level_is_pr_only_for_a_human_line_citing_a_pull_request_in_its_form(mark, source, level):
    assert decisions.level({"mark": mark, "source": source}) == level


def test_cite_prints_the_citation_of_a_line_committed_at_the_kit_commit_and_refuses_one_that_is_not(kit):
    git(kit["root"], "init", "-q")
    git(kit["root"], "commit", "-q", "--allow-empty", "-m", "start")
    assert proxy() == 0
    assert agentkit("cite", "K-0001").returncode == 2
    git(kit["root"], "add", "decisions/log.jsonl")
    git(kit["root"], "commit", "-q", "-m", "the decision")
    cited = agentkit("cite", "K-0001")
    assert cited.returncode == 0, cited.stderr
    head = git(kit["root"], "rev-parse", "HEAD")
    assert cited.stdout == f"kit:chadlis/agentkit@{head}:decisions/log.jsonl#K-0001;mark=PROXY;level=none\n"
    assert not list(CITATION.iter_errors(cited.stdout.removesuffix("\n")))
    assert agentkit("cite", "K-0002").returncode == 2


def test_status_lists_the_decisions_with_their_marks_and_levels_and_the_open_stops(kit, capsys):
    assert proxy() == 0
    assert human(kit, "--pull-request", PR) == 0
    capsys.readouterr()
    assert main(["status"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert "K-0001 PROXY (level none, rule keep-higher-dev-score@1): Resume after stop STOP-r-0002? -> resume" in printed
    assert "K-0002 HUMAN (level pr): Resume after stop STOP-r-0002? -> resume" in printed
    # K-0002 cites the stop, so it is resumed: no stop is open.
    assert "Open stops: none" in printed
    assert WORDS.strip() not in "\n".join(printed)


def conductor_marker(kit, pid: int) -> Path:
    """A conductor marker file, as the conductor writes it when its loop starts."""
    marker = kit["root"] / "ledger" / "conductor" / "c-0001.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"pid": pid, "start_time": "2026-11-27T00:00:00Z"}), encoding="utf-8")
    return marker


def test_while_a_conductor_marker_names_a_live_process_decide_refuses_human_whatever_agentkit_process_says(kit):
    conductor_marker(kit, os.getpid())
    for env in ({}, {decisions.PROCESS_VARIABLE: "cli"}):
        refused = agentkit("decide", "--mark", "human", "--words-file", kit["words"], "--stop", "STOP-r-0002", "--question", "q", "--option", "o", env=env)
        assert refused.returncode == 2, refused.stdout + refused.stderr
    assert human(kit) == 2
    assert log(kit) == []
    assert proxy() == 0
    assert [line["mark"] for line in log(kit)] == ["PROXY"]


def test_a_stale_conductor_marker_whose_process_is_gone_is_reported_and_ignored(kit):
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
    marker = conductor_marker(kit, int(finished.stdout))
    written = agentkit("decide", "--mark", "human", "--words-file", kit["words"], "--stop", "STOP-r-0002", "--question", "q", "--option", "o")
    assert written.returncode == 0, written.stdout + written.stderr
    assert str(marker.relative_to(kit["root"])) in written.stderr
    assert [line["mark"] for line in log(kit)] == ["HUMAN"]


RELAY_SOURCE = "https://github.com/example/kit/issues/1#issuecomment-42"
RELAYED_BY = "fixture-agent"


def relay(*extra, words=WORDS, source=RELAY_SOURCE, relayed_by=RELAYED_BY):
    arguments = ["--mark", "relayed", *extra]
    for option, value in (("--words", words), ("--source", source), ("--relayed-by", relayed_by)):
        if value is not None:
            arguments += [option, value]
    return decide(*arguments)


@pytest.mark.parametrize(("fields", "reason"), [
    ({"words": None}, "RELAYED_WITHOUT_WORDS"),
    ({"words": " \n"}, "RELAYED_WITHOUT_WORDS"),
    ({"source": None}, "RELAYED_WITHOUT_SOURCE"),
    ({"source": "http://example.invalid/message"}, "RELAYED_SOURCE_FORM"),
    ({"source": "https:///message"}, "RELAYED_SOURCE_FORM"),
    ({"source": "https://sender@example.invalid/message"}, "RELAYED_SOURCE_FORM"),
    ({"relayed_by": None}, "RELAYED_WITHOUT_RELAY"),
    ({"relayed_by": " \t"}, "RELAYED_WITHOUT_RELAY"),
])
def test_required_relayed_fields_are_refused_without_a_log_write(kit, capsys, fields, reason):
    assert relay(**fields) == 2
    assert reason in capsys.readouterr().err
    assert log(kit) == []


@pytest.mark.parametrize("role", ["conductor", "evaluator"])
def test_a_marked_process_cannot_write_relayed_even_when_cli_is_passed_as_writer(kit, capsys, monkeypatch, role):
    monkeypatch.setenv(decisions.PROCESS_VARIABLE, role)
    assert relay() == 2
    assert "RELAYED_REFUSED" in capsys.readouterr().err
    with pytest.raises(decisions.DecisionError) as error:
        decisions.append(
            question="q", option="o", mark="RELAYED", source={}, by="cli",
            words=WORDS, source_url=RELAY_SOURCE, relayed_by=RELAYED_BY,
        )
    assert error.value.code == "RELAYED_REFUSED"
    assert log(kit) == []


def test_a_conductor_marker_refuses_a_relay_from_an_unmarked_cli(kit, capsys):
    conductor_marker(kit, os.getpid())
    assert relay() == 2
    assert "CONDUCTOR_RUNNING" in capsys.readouterr().err
    assert log(kit) == []


def test_a_relay_keeps_its_verbatim_words_and_committed_citation(kit, capsys):
    assert relay() == 0
    [line] = log(kit)
    assert (line["mark"], line["words"], line["source"], line["by"], line["relayed_by"], line["rule"]) == (
        "RELAYED", WORDS, RELAY_SOURCE, "cli", RELAYED_BY, "owner-relayed@1",
    )
    assert not list(LINE.iter_errors(line))
    assert decisions.level(line) == "relayed"
    assert "level" not in line
    assert WORDS not in capsys.readouterr().out
    git(kit["root"], "init", "-q")
    git(kit["root"], "add", "decisions/log.jsonl")
    git(kit["root"], "commit", "-q", "-m", "the relayed fixture")
    head = git(kit["root"], "rev-parse", "HEAD")
    cited = agentkit("cite", line["id"])
    assert cited.returncode == 0, cited.stderr
    assert cited.stdout == f"kit:chadlis/agentkit@{head}:decisions/log.jsonl#K-0001;mark=RELAYED;level=relayed\n"
    assert not list(CITATION.iter_errors(cited.stdout.rstrip("\n")))


def test_status_displays_relayed_under_its_own_mark_and_does_not_resume_a_human_stop(kit, capsys):
    assert relay() == 0
    capsys.readouterr()
    assert main(["status"]) == 0
    status = capsys.readouterr().out
    assert "K-0001 RELAYED (level relayed" in status
    assert "HUMAN" not in status and WORDS not in status
    assert "Open stops: STOP-r-0002 (failed-share@1, run r-0002)" in status

    assert main(["status", "--json"]) == 0
    packed = json.loads(capsys.readouterr().out)
    status_schema = ROOT / "contracts" / "kit-status-1" / "status.schema.json"
    jsonschema.validate(packed, json.loads(status_schema.read_text(encoding="utf-8")))
    assert packed["gate"] is None
    assert [stop["id"] for stop in packed["open_stops"]] == ["STOP-r-0002"]
    [line] = packed["decisions"]
    assert (line["mark"], line["level"]) == ("RELAYED", "relayed")
    assert "words" not in line and "source" not in line


@pytest.mark.parametrize("legacy_option", [
    ("--run", "r-0001"), ("--stop", "STOP-r-0002"),
    ("--pull-request", PR), ("--rule", "owner-relayed@1"),
])
def test_a_relay_refuses_legacy_decision_options(kit, capsys, legacy_option):
    assert relay(*legacy_option) == 2
    assert "RELAYED_WITH_LEGACY_OPTIONS" in capsys.readouterr().err
    assert log(kit) == []


def test_legacy_marks_refuse_relay_options(kit, capsys):
    assert proxy("--words", WORDS) == 2
    assert "RELAY_OPTIONS_REFUSED" in capsys.readouterr().err
    assert human(kit, "--source", RELAY_SOURCE) == 2
    assert "RELAY_OPTIONS_REFUSED" in capsys.readouterr().err
    assert log(kit) == []
