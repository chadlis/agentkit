"""The `scripted` adapter: replays the answer an authored fixture holds for a Case, with no model."""

from dataclasses import dataclass
from pathlib import Path

from agentkit.cases import Case, WorkloadError, read_json_lines

ORIGIN = "authored_fixture"
# What a fixture line may declare of its answer, carried to the exported line as it is.
ANSWER_KEYS = ("observation_status", "raw_answer", "outcome", "usage", "metadata")


@dataclass(frozen=True)
class Answer:
    fields: dict


class ScriptedAdapter:
    def __init__(self, fixture: Path):
        self._answers: dict[tuple[str, int], dict] = {}
        settings = []
        for row in read_json_lines(fixture):
            key = (row.get("case_id"), row.get("repeat"))
            if not isinstance(key[0], str) or not isinstance(key[1], int) or key in self._answers:
                raise WorkloadError(f"{fixture}: each line needs a case_id and a repeat, once per pair ({key!r})")
            if row.get("observation_status") == "produced" and not isinstance(row.get("raw_answer"), str):
                raise WorkloadError(f"{fixture}: the produced line {key!r} has no raw_answer")
            self._answers[key] = {name: row[name] for name in ANSWER_KEYS if name in row}
            settings.append(row.get("settings"))
        if any(item != settings[0] for item in settings):
            raise WorkloadError(f"{fixture}: the lines do not declare the same settings")
        # The run's settings are the ones the fixture declares for every line.
        self.settings: dict | None = settings[0] if settings else None

    def answer(self, case: Case, repeat: int) -> Answer | None:
        """The fixture's answer for (Case, Repeat), or None when the fixture holds none."""
        fields = self._answers.get((case.case_id, repeat))
        return None if fields is None else Answer(fields=dict(fields))
