"""The decision rules of `agentkit.rules`. Offline; no model call."""

from agentkit.rules import keep_higher_dev_score


def test_keep_higher_dev_score_keeps_the_higher_side_and_the_control_on_a_tie():
    assert keep_higher_dev_score(control=1.0, candidate=0.6) == "keep_control"
    assert keep_higher_dev_score(control=0.6, candidate=1.0) == "keep_candidate"
    assert keep_higher_dev_score(control=0.8, candidate=0.8) == "keep_control"
