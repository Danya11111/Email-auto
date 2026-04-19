from __future__ import annotations

from typing import Sequence

from app.application.dtos import IncomingMessageDTO
from app.application.ports import MessageReaderPort


class AppleMailExportReader(MessageReaderPort):
    """Placeholder adapter for future Apple Mail integration.

    Planned approach (not implemented in MVP):
    - Use AppleScript / Shortcuts / Automation to export selected mailboxes to `.eml` on disk.
    - Avoid reading undocumented Apple Mail internal databases directly (fragile + privacy-sensitive).

    This stub returns an empty batch so composition roots can wire the port safely.
    """

    def read_messages(self) -> Sequence[IncomingMessageDTO]:
        return ()
