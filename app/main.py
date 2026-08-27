import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.meta import MetaAPIError, safe_filename, send_media, send_text, verify_signature
from app.migrations import ensure_phase2_schema
from app.models import Agent, Conversation, Message
from app.webhook import process_webhook

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = Path("data/media")
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
logger = logging.getLogger("uvicorn.error")

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_phase2_schema()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Webhook diagnostics: app_secret_configured=%s verify_token_configured=%s",
        bool(settings.meta_app_secret),
        bool(settings.whatsapp_verify_token),
    )


def _prepare_messages(messages: list[Message]) -> list[Message]:
    by_wamid = {message.wamid: message for message in messages if message.wamid}

    for message in messages:
        message.media_url = None
        message.media_filename = None
        message.media_mime_type = None
        message.media_size = None
        message.reply_to_wamid = None
        message.reply_preview = None

        if not message.raw_payload:
            continue

        try:
            payload = json.loads(message.raw_payload)
        except (TypeError, json.JSONDecodeError):
            continue

        if not isinstance(payload, dict):
            continue

        media = payload.get("local_media")
        if isinstance(media, dict):
            message.media_url = media.get("url")
            message.media_filename = media.get("filename")
            message.media_mime_type = media.get("mime_type")
            message.media_size = media.get("size")

        message.reply_to_wamid = payload.get("reply_to")
        webhook_message = payload.get("webhook_message")
        if isinstance(webhook_message, dict):
            context = webhook_message.get("context") or {}
            message.reply_to_wamid = message.reply_to_wamid or context.get("id")

    for message in messages:
        if not message.reply_to_wamid:
            continue
        original = by_wamid.get(message.reply_to_wamid)
        if original:
            message.reply_preview = original.body or f"[{original.message_type} message]"
        else:
            message.reply_preview = "Earlier WhatsApp message"

    return messages


def _message_type_for_upload(mime_type: str) -> str:
    mime_type = (mime_type or "application/octet-stream").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    return "document"


def _conversation_redirect(conversation_id: int) -> RedirectResponse:
    return RedirectResponse(url=f"/?conversation={conversation_id}", status_code=303)


def _live_fingerprint() -> str:
    """Build the SSE change fingerprint in a worker thread, not the event loop."""
    with SessionLocal() as db:
        conversations = db.execute(
            select(
                Conversation.id,
                Conversation.unread_count,
                Conversation.last_message_at,
                Conversation.assigned_agent_id,
                Conversation.is_archived,
            ).order_by(Conversation.id)
        ).all()
        messages = db.execute(
            select(Message.id, Message.status)
            .order_by(Message.id.desc())
            .limit(150)
        ).all()

    return json.dumps(
        {
            "conversations": [tuple(str(value) for value in row) for row in conversations],
            "messages": [tuple(str(value) for value in row) for row in messages],
        },
        sort_keys=True,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "phase": 2}


@app.get("/", response_class=HTMLResponse)
def inbox(
    request: Request,
    conversation: int | None = None,
    q: str = "",
    filter: str = "open",
    db: Session = Depends(get_db),
):
    query = select(Conversation).order_by(Conversation.last_message_at.desc())
    q = q.strip()

    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                Conversation.display_name.ilike(pattern),
                Conversation.wa_id.ilike(pattern),
                Conversation.messages.any(Message.body.ilike(pattern)),
            )
        )

    if filter == "unread":
        query = query.where(Conversation.is_archived.is_(False), Conversation.unread_count > 0)
    elif filter == "archived":
        query = query.where(Conversation.is_archived.is_(True))
    elif filter != "all":
        filter = "open"
        query = query.where(Conversation.is_archived.is_(False))

    conversations = db.scalars(query).all()
    agents = db.scalars(select(Agent).where(Agent.active.is_(True)).order_by(Agent.name)).all()
    selected = None
    messages: list[Message] = []

    if conversation is not None:
        selected = db.get(Conversation, conversation)
    elif conversations:
        selected = conversations[0]

    if selected:
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == selected.id)
            .order_by(Message.timestamp.asc(), Message.id.asc())
        ).all()
        selected.unread_count = 0
        db.commit()

    _prepare_messages(messages)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "conversations": conversations,
            "selected": selected,
            "messages": messages,
            "agents": agents,
            "app_name": settings.app_name,
            "search_query": q,
            "conversation_filter": filter,
        },
    )


@app.post("/send")
async def send_message(
    to: str = Form(...),
    body: str = Form(""),
    reply_to: str = Form(""),
    attachment: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    to = "".join(ch for ch in to if ch.isdigit())
    body = body.strip()
    reply_to = reply_to.strip() or None
    has_attachment = bool(attachment and attachment.filename)

    if not to or (not body and not has_attachment):
        raise HTTPException(status_code=400, detail="Recipient and message or attachment are required")

    conversation = db.scalar(select(Conversation).where(Conversation.wa_id == to))
    if conversation is None:
        conversation = Conversation(wa_id=to, display_name=to)
        db.add(conversation)
        db.flush()

    local_media = None
    message_type = "text"

    try:
        if has_attachment and attachment is not None:
            content = await attachment.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Attachment exceeds the 25 MB POC limit")

            mime_type = attachment.content_type or "application/octet-stream"
            message_type = _message_type_for_upload(mime_type)
            original_name = safe_filename(attachment.filename or "attachment")
            local_name = f"{uuid4().hex}_{original_name}"
            local_path = MEDIA_DIR / local_name
            local_path.write_bytes(content)
            local_media = {
                "url": f"/media/{local_name}",
                "filename": original_name,
                "mime_type": mime_type,
                "size": len(content),
            }

            result = await send_media(
                to=to,
                content=content,
                filename=original_name,
                mime_type=mime_type,
                message_type=message_type,
                caption=body or None,
                reply_to=reply_to,
            )
        else:
            result = await send_text(to, body, reply_to=reply_to)
    except MetaAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    wamid = result.get("messages", [{}])[0].get("id") if result.get("messages") else None
    now = datetime.now(timezone.utc)
    display_body = body or ((local_media or {}).get("filename") if message_type == "document" else f"[{message_type} message]")
    stored_payload = {
        "meta_response": result,
        "local_media": local_media,
        "reply_to": reply_to,
    }
    db.add(
        Message(
            conversation_id=conversation.id,
            wamid=wamid,
            direction="outbound",
            message_type=message_type,
            body=display_body,
            status="sent",
            timestamp=now,
            raw_payload=json.dumps(stored_payload),
        )
    )
    conversation.last_message_at = now
    conversation.is_archived = False
    db.commit()
    return _conversation_redirect(conversation.id)


@app.post("/agents")
def create_agent(name: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Agent name is required")
    existing = db.scalar(select(Agent).where(Agent.name == name))
    if not existing:
        db.add(Agent(name=name))
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/conversations/{conversation_id}/assign")
def assign_conversation(
    conversation_id: int,
    agent_id: str = Form(""),
    db: Session = Depends(get_db),
):
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation.assigned_agent_id = int(agent_id) if agent_id.isdigit() else None
    db.commit()
    return _conversation_redirect(conversation_id)


@app.post("/conversations/{conversation_id}/archive")
def archive_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation.is_archived = not conversation.is_archived
    db.commit()
    return _conversation_redirect(conversation_id)


@app.post("/conversations/{conversation_id}/notes")
def add_internal_note(
    conversation_id: int,
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    body = body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Note cannot be empty")
    db.add(
        Message(
            conversation_id=conversation_id,
            direction="internal",
            message_type="note",
            body=body,
            status="local",
            timestamp=datetime.now(timezone.utc),
            raw_payload=json.dumps({"internal_note": True}),
        )
    )
    db.commit()
    return _conversation_redirect(conversation_id)


@app.get("/events")
async def live_events():
    async def event_stream():
        previous = None
        try:
            while True:
                fingerprint = await asyncio.to_thread(_live_fingerprint)

                if previous is None:
                    previous = fingerprint
                elif fingerprint != previous:
                    previous = fingerprint
                    yield "event: refresh\ndata: changed\n\n"
                else:
                    yield ": keepalive\n\n"

                await asyncio.sleep(3)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/webhook", response_class=PlainTextResponse)
def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    if mode == "subscribe" and verify_token == settings.whatsapp_verify_token and challenge:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    signature_present = bool(signature)
    signature_scheme = signature.split("=", 1)[0] if signature and "=" in signature else None
    signature_length = len(signature) if signature else 0
    secret_configured = bool(settings.meta_app_secret)
    signature_valid = verify_signature(body, signature)

    logger.info(
        "WhatsApp webhook signature diagnostic: signature_header_present=%s signature_scheme=%s signature_length=%d body_length=%d app_secret_configured=%s signature_valid=%s client=%s",
        signature_present,
        signature_scheme,
        signature_length,
        len(body),
        secret_configured,
        signature_valid,
        request.client.host if request.client else "unknown",
    )

    if not signature_valid:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    await process_webhook(db, payload)
    return {"status": "ok"}
