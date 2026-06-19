"""Active-window (quiet-hours) gating for opportunistic hosts.

Borrowed lab desktops are often only fair game at night. A :class:`Window`
describes a daily span like ``19:00-09:30`` during which a host may run work;
outside it the watchdog pauses the job so the owner gets their machine back.
Windows may wrap past midnight (``start > end``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from yuj.exceptions import YujError

_MINUTES_PER_DAY = 24 * 60
_WINDOW_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True)
class Window:
    """A daily run window, as minutes since midnight in ``[0, 1440)``.

    A window where ``start == end`` means "always on" (the full 24 hours). When
    ``start > end`` the window wraps past midnight (e.g. ``19:00-09:30``).
    """

    start_min: int
    end_min: int

    def __post_init__(self) -> None:
        for value in (self.start_min, self.end_min):
            if not 0 <= value < _MINUTES_PER_DAY:
                raise YujError(
                    f"window minute {value} out of range",
                    hint="minutes since midnight must be in [0, 1440)",
                )

    @classmethod
    def parse(cls, text: str) -> Window:
        """Parse ``"HH:MM-HH:MM"`` into a :class:`Window`.

        Raises:
            YujError: If the text is not two ``HH:MM`` times separated by ``-``,
                or an hour/minute is out of range.
        """
        match = _WINDOW_RE.match(text)
        if not match:
            raise YujError(
                f"invalid active_window {text!r}",
                hint="expected 'HH:MM-HH:MM', e.g. '19:00-09:30'",
            )
        sh, sm, eh, em = (int(g) for g in match.groups())
        if sh > 23 or eh > 23 or sm > 59 or em > 59:
            raise YujError(
                f"invalid time in active_window {text!r}",
                hint="hours 00-23, minutes 00-59",
            )
        return cls(sh * 60 + sm, eh * 60 + em)

    @property
    def always_on(self) -> bool:
        """True when the window spans the full day (``start == end``)."""
        return self.start_min == self.end_min

    @property
    def wraps_midnight(self) -> bool:
        """True when the window runs past midnight (``start > end``)."""
        return self.start_min > self.end_min

    def contains_minute(self, minute_of_day: int) -> bool:
        """Whether ``minute_of_day`` (0-1439) falls inside the window."""
        if self.always_on:
            return True
        if self.start_min < self.end_min:
            return self.start_min <= minute_of_day < self.end_min
        return minute_of_day >= self.start_min or minute_of_day < self.end_min

    @property
    def label(self) -> str:
        """Human-readable ``"HH:MM-HH:MM"`` form."""
        return f"{_fmt(self.start_min)}-{_fmt(self.end_min)}"


def _fmt(minute_of_day: int) -> str:
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"
