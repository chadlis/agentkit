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
