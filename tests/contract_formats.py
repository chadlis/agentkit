"""Format checks for the locked JSON Schema test environment."""

import re
from datetime import datetime

from jsonschema import FormatChecker

FORMATS = FormatChecker()
DATE_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


# jsonschema's built-in date-time checker needs an optional dependency.
@FORMATS.checks("date-time", raises=ValueError)
def _date_time(value):
    if not isinstance(value, str):
        return True  # The schema checks the type.
    if DATE_TIME.fullmatch(value) is None:
        return False
    datetime.fromisoformat(value.upper())
    return True
