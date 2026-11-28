"""Check the public evidence of committed RELAYED lines when explicitly online."""

import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pytest

from agentkit import decisions

ROOT = Path(__file__).resolve().parents[1]
ROUND_3 = "https://github.com/chadlis/hypotrail/issues/75#issuecomment-7223332432"
# Only explicitly reviewed public owner comments can be fetched. The log is not a list of trusted network destinations.
REVIEWED = {ROUND_3: "https://api.github.com/repos/chadlis/hypotrail/issues/comments/7223332432"}
ISSUE = "https://api.github.com/repos/chadlis/hypotrail/issues/75"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def comment_body(source):
    if source not in REVIEWED:
        pytest.fail("Relay cites an unreviewed public source", pytrace=False)
    request = Request(REVIEWED[source], headers={"Accept": "application/vnd.github+json", "User-Agent": "agentkit-relay-test"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            document = json.loads(response.read(1024 * 1024))
    except (URLError, TimeoutError, ValueError):
        pytest.fail("Could not read the reviewed public GitHub source", pytrace=False)
    if (not isinstance(document, dict) or document.get("issue_url") != ISSUE
            or document.get("html_url") != source or not isinstance(document.get("body"), str)):
        pytest.fail("The GitHub source response did not identify a comment on the decision log", pytrace=False)
    return document["body"]


@pytest.fixture
def relays():
    return [line for line in decisions.lines(ROOT / decisions.LOG) if line.get("mark") == "RELAYED"]


def test_committed_relay_rule_uses_the_round_3_owner_message(relays):
    assert any(line["source"] == ROUND_3 and line["option"] == "Adopt the relayed owner-words mark" for line in relays)


@pytest.mark.skipif(os.environ.get("AGENTKIT_VERIFY_RELAYED_SOURCES") != "1"
                    or os.environ.get("AGENTKIT_SOCKETS_BLOCKED") == "1",
                    reason="offline by default: set AGENTKIT_VERIFY_RELAYED_SOURCES=1 to check the cited public comments")
def test_each_committed_relay_quotes_its_public_source_verbatim(relays):
    assert relays
    bodies = {source: comment_body(source) for source in {line["source"] for line in relays}}
    for line in relays:
        if line["words"] not in bodies[line["source"]]:
            pytest.fail(f"{line['id']} does not quote its public source verbatim", pytrace=False)
