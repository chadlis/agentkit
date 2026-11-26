"""No test reaches the network, and CI hands the tests no secret (AGENTS.md: "None runs in CI or in a test")."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
INNER = "AGENTKIT_SOCKETS_BLOCKED"
# Put first on the PYTHONPATH of the inner suite: every network socket call is logged, then refused.
BLOCK_SOCKETS = '''
import os, socket, sys

_log = os.environ["AGENTKIT_SOCKET_LOG"]


def _hook(event, args):
    network = event == "socket.getaddrinfo" or (event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6))
    if network:
        with open(_log, "a", encoding="utf-8") as log:
            log.write(f"{event} {args[1:]!r}\\n")
        raise OSError(f"agentkit tests block the network: {event}")


sys.addaudithook(_hook)
'''


def test_the_whole_suite_runs_with_every_socket_blocked_and_attempts_no_network_call(tmp_path):
    if os.environ.get(INNER) == "1":
        return  # the inner run of this same test
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(BLOCK_SOCKETS, encoding="utf-8")
    log = tmp_path / "sockets.log"
    env = dict(os.environ, AGENTKIT_SOCKET_LOG=str(log), **{INNER: "1"})
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(blocker), os.environ.get("PYTHONPATH")]))
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=900)
    attempts = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    assert attempts == []
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def test_the_ci_workflow_passes_no_secret():
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert re.findall(r"secrets\.|ANTHROPIC|API_KEY", text) == []


def test_the_e2e_workflow_passes_no_secret():
    text = (WORKFLOWS / "e2e.yml").read_text(encoding="utf-8")
    assert re.findall(r"secrets\.|ANTHROPIC|API_KEY", text) == []
