"""The evaluator workflow, `.github/workflows/evaluator.yml` (`docs/design.md`, section 3): a person starts it by hand, in a protected GitHub environment, and the scripted adapter it runs needs no secret. Offline; no model call."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "evaluator.yml"


def triggers(workflow: dict) -> dict:
    # YAML 1.1 reads the bare key `on` as the boolean true.
    found = workflow.get("on", workflow.get(True))
    return {found: None} if isinstance(found, str) else {name: None for name in found} if isinstance(found, list) else found


def test_the_evaluator_workflow_runs_on_workflow_dispatch_only_and_each_job_in_an_environment():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    started_by = set(triggers(workflow))
    assert started_by == {"workflow_dispatch"}
    jobs = workflow["jobs"]
    assert jobs and all(job.get("environment") for job in jobs.values())


def test_the_evaluator_workflow_passes_no_secret():
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")


def steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def test_the_evaluator_workflow_holds_contents_read_only():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    assert [name for name, job in workflow["jobs"].items() if "permissions" in job] == []


def test_the_evaluator_workflow_checks_out_the_kit_and_hypotrail_with_their_whole_history_to_date_readings_and_candidates():
    checkouts = [step for step in steps(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))) if step.get("uses", "").startswith("actions/checkout@")]
    assert [(step.get("with", {}).get("repository", "kit"), step.get("with", {}).get("fetch-depth")) for step in checkouts] == [("kit", 0), ("chadlis/hypotrail", 0)]


def test_the_evaluator_workflow_uploads_the_readings_line_and_shows_it_in_the_summary_after_a_failed_reading_too():
    after_any_reading = [step for step in steps(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))) if "always()" in str(step.get("if", ""))]
    uploads = [step["with"]["path"] for step in after_any_reading if step.get("uses", "").startswith("actions/upload-artifact@")]
    shown = [step["run"] for step in after_any_reading if "GITHUB_STEP_SUMMARY" in step.get("run", "")]
    assert (uploads, len(shown), all("published/reading.jsonl" in text for text in shown)) == (["published/reading.jsonl"], 1, True)
