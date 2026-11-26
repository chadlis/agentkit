"""The kit's named decision rules, each applied by a `PROXY` decision that names it."""

KEEP_HIGHER_DEV_SCORE = "keep-higher-dev-score@1"


def keep_higher_dev_score(control: float, candidate: float) -> str:
    """The option id of `keep_after_comparison` that keep-higher-dev-score@1 chooses from the two dev scores; a tie keeps the Control."""
    return "keep_candidate" if candidate > control else "keep_control"
