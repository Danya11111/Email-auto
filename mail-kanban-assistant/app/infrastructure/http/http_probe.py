from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.application.ports import HttpProbePort


class UrllibHttpProbe(HttpProbePort):
    def get_status(self, url: str, *, timeout_seconds: float) -> int | None:
        try:
            req = Request(url, method="GET", headers={"User-Agent": "mail-kanban-assistant/doctor"})
            with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 - controlled doctor probe URL
                return int(getattr(resp, "status", 200))
        except HTTPError as exc:
            return int(exc.code)
        except URLError:
            return None
        except TimeoutError:
            return None
        except OSError:
            return None
