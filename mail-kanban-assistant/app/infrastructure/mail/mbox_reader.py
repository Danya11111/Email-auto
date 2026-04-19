from __future__ import annotations

import mailbox
from pathlib import Path
from typing import Sequence

from app.application.dtos import IncomingMessageDTO
from app.application.ports import MessageReaderPort
from app.domain.enums import MessageSource
from app.infrastructure.mail.eml_reader import (
    _all_addrs,
    _decode_payload,
    _first_addr,
    _parse_date,
    _rfc_message_id,
    _thread_hint,
)
from app.utils.ids import stable_dedupe_key_for_incoming


class MboxFileReader(MessageReaderPort):
    def __init__(self, mbox_path: Path) -> None:
        self._mbox_path = mbox_path

    def read_messages(self) -> Sequence[IncomingMessageDTO]:
        if not self._mbox_path.exists():
            return ()

        mbox = mailbox.mbox(str(self._mbox_path))
        out: list[IncomingMessageDTO] = []
        try:
            for key in mbox.keys():
                msg = mbox[key]
                body = _decode_payload(msg)
                rfc_id = _rfc_message_id(msg)
                received = _parse_date(msg)
                dedupe = stable_dedupe_key_for_incoming(
                    source=MessageSource.MBOX.value,
                    rfc_message_id=rfc_id,
                    subject=msg.get("Subject"),
                    sender=_first_addr(msg, "From"),
                    received_at_iso=received.isoformat() if received else None,
                    body_plain=body,
                )
                out.append(
                    IncomingMessageDTO(
                        dedupe_key=dedupe,
                        source=MessageSource.MBOX,
                        rfc_message_id=rfc_id,
                        subject=msg.get("Subject"),
                        sender=_first_addr(msg, "From"),
                        recipients=_all_addrs(msg, "To") + _all_addrs(msg, "Cc"),
                        received_at=received,
                        body_plain=body,
                        thread_hint=_thread_hint(msg),
                        source_path=str(self._mbox_path),
                    )
                )
        finally:
            mbox.close()

        return tuple(out)
