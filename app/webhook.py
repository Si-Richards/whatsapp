import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Message


def _timestamp(value: str | None) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _conversation(db: Session, wa_id: str, display_name: str | None = None) -> Conversation:
    item = db.scalar(select(Conversation).where(Conversation.wa_id == wa_id))
    if item is None:
        item = Conversation(wa_id=wa_id, display_name=display_name or wa_id)
        db.add(item)
        db.flush()
    elif display_name:
        item.display_name = display_name
    return item


def process_webhook(db: Session, payload: dict) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            contacts = {
                c.get("wa_id"): c.get("profile", {}).get("name")
                for c in value.get("contacts", [])
            }

            for msg in value.get("messages", []):
                wamid = msg.get("id")
                if wamid and db.scalar(select(Message).where(Message.wamid == wamid)):
                    continue

                wa_id = msg.get("from")
                if not wa_id:
                    continue
                conv = _conversation(db, wa_id, contacts.get(wa_id))
                msg_type = msg.get("type", "unknown")
                body = msg.get("text", {}).get("body")
                if body is None and msg_type != "text":
                    body = f"[{msg_type} message]"

                when = _timestamp(msg.get("timestamp"))
                db.add(Message(
                    conversation_id=conv.id,
                    wamid=wamid,
                    direction="inbound",
                    message_type=msg_type,
                    body=body,
                    status="received",
                    timestamp=when,
                    raw_payload=json.dumps(msg),
                ))
                conv.last_message_at = when
                conv.unread_count += 1

            for status in value.get("statuses", []):
                wamid = status.get("id")
                if not wamid:
                    continue
                stored = db.scalar(select(Message).where(Message.wamid == wamid))
                if stored:
                    stored.status = status.get("status", stored.status)

    db.commit()
