from __future__ import annotations

from datetime import datetime

from app.application.ports import ClockPort
from app.utils.time import utc_now


class SystemClock(ClockPort):
    def now(self) -> datetime:
        return utc_now()
