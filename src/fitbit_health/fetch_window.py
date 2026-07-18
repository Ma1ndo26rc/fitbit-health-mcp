from typing import Literal, TypeAlias


FetchDays: TypeAlias = Literal[14, 7, 3, 1]
ALLOWED_FETCH_DAYS: tuple[int, ...] = (14, 7, 3, 1)
DEFAULT_FETCH_DAYS: FetchDays = 7
FETCH_DAYS_ERROR = "days must be one of 14, 7, 3, or 1."


def is_allowed_fetch_days(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value in ALLOWED_FETCH_DAYS
    )
