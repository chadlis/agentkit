"""The assistant adapters: each turns a Case into an answer."""


class RunStopped(Exception):
    """An adapter stops the run: the harness records `fields` for the current pair when given, and closes the run `failed` with `reason`."""

    def __init__(self, reason: str, message: str, fields: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.fields = fields
