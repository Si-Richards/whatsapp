import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.meta import MetaAPIError, download_media, mark_message_read
from app.models import Conversation, Message


logger = logging.getLogger("uvicorn.error")
MEDIA_TYPES = {"image", "document", "audio", "video", "sticker"}


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
    item.is_archived = False
    return item


async def _media_for_message(msg: dict, msg_type: str) -> dict | None:
    if msg_type not in MEDIA_TYPES:
        return None

    media = msg.get(msg_type) or {}
    media_id = media.get("id")
    if not media_id:
        return None

    preferred_filename = media.get("filename") if msg_type == "document" else None
    try:
        return await download_media(media_id, preferred_filename=preferred_filename)
    except MetaAPIError as exc:
        logger.warning("Unable to download WhatsApp media id=%s: %s", media_id, exc)
        return {
            "media_id": media_id,
            "mime_type": media.get("mime_type"),
            "filename": preferred_filename,
            "url": None,
            "error": str(exc),
        }


def _message_body(msg: dict, msg_type: str, media_info: dict | None) -> str | None:
    if msg_type == "text":
        return msg.get("text", {}).get("body")

    if msg_type in {"image", "video"}:
        caption = msg.get(msg_type, {}).get("caption")
        if caption:
            return caption

    if msg_type == "document":
        caption = msg.get("document", {}).get("caption")
        if caption:
            return caption
        filename = (media_info or {}).get("filename") or msg.get("document", {}).get("filename")
        if filename:
            return filename

    return f"[{msg_type} message]"


async def process_webhook(db: Session, payload: dict) -> None:
    read_ids: list[str] = []

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
                    read_ids.append(wamid)
                    continue

                wa_id = msg.get("from")
                if not wa_id:
                    continue

                conv = _conversation(db, wa_id, contacts.get(wa_id))
                msg_type = msg.get("type", "unknown")
                media_info = await _media_for_message(msg, msg_type)
                body = _message_body(msg, msg_type, media_info)
                when = _timestamp(msg.get("timestamp"))

                stored_payload = {
                    "webhook_message": msg,
                    "local_media": media_info,
                }

                db.add(
                    Message(
                        conversation_id=conv.id,
                        wamid=wamid,
                        direction="inbound",
                        message_type=msg_type,
                        body=body,
                        status="received",
                        timestamp=when,
                        raw_payload=json.dumps(stored_payload),
                    )
                )
                conv.last_message_at = when
                conv.unread_count += 1

                if wamid:
                    read_ids.append(wamid)

            for status in value.get("statuses", []):
                wamid = status.get("id")
                if not wamid:
                    continue
                stored = db.scalar(select(Message).where(Message.wamid == wamid))
                if stored:
                    stored.status = status.get("status", stored.status)

    db.commit()

    for wamid in dict.fromkeys(read_ids):
        try:
            await mark_message_read(wamid)
            stored = db.scalar(select(Message).where(Message.wamid == wamid))
            if stored and stored.direction == "inbound":
                stored.status = "read"
        except MetaAPIError as exc:
            logger.warning("Unable to mark WhatsApp message read id=%s: %s", wamid, exc)

    db.commit()
