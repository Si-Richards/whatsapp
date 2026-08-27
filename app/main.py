import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db
from app.meta import MetaAPIError, send_text, verify_signature
from app.models import Conversation, Message
from app.webhook import process_webhook

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = Path("data/media")
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("uvicorn.error")

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Webhook diagnostics: app_secret_configured=%s verify_token_configured=%s",
        bool(settings.meta_app_secret),
        bool(settings.whatsapp_verify_token),
    )


def _prepare_messages(messages: list[Message]) -> list[Message]:
    """Attach non-persistent display metadata parsed from raw_payload."""
    for message in messages:
        message.media_url = None
        message.media_filename = None
        message.media_mime_type = None
        message.media_size = None

        if not message.raw_payload:
            continue

        try:
            payload = json.loads(message.raw_payload)
        except (TypeError, json.JSONDecodeError):
            continue

        media = payload.get("local_media") if isinstance(payload, dict) else None
        if not isinstance(media, dict):
            continue

        message.media_url = media.get("url")
        message.media_filename = media.get("filename")
        message.media_mime_type = media.get("mime_type")
        message.media_size = media.get("size")

    return messages


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.get("/", response_class=HTMLResponse)
def inbox(request: Request, conversation: int | None = None, db: Session = Depends(get_db)):
    conversations = db.scalars(
        select(Conversation).order_by(Conversation.last_message_at.desc())
    ).all()
    selected = None
    messages: list[Message] = []

    if conversation is not None:
        selected = db.get(Conversation, conversation)
        if selected:
            messages = db.scalars(
                select(Message)
                .where(Message.conversation_id == selected.id)
                .order_by(Message.timestamp.asc())
            ).all()
            selected.unread_count = 0
            db.commit()
    elif conversations:
        selected = conversations[0]
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == selected.id)
            .order_by(Message.timestamp.asc())
        ).all()

    _prepare_messages(messages)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "conversations": conversations,
            "selected": selected,
            "messages": messages,
            "app_name": settings.app_name,
        },
    )


@app.post("/send")
async def send_message(
    to: str = Form(...),
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    to = "".join(ch for ch in to if ch.isdigit())
    body = body.strip()
    if not to or not body:
        raise HTTPException(status_code=400, detail="Recipient and message are required")

    try:
        result = await send_text(to, body)
    except MetaAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    conversation = db.scalar(select(Conversation).where(Conversation.wa_id == to))
    if conversation is None:
        conversation = Conversation(wa_id=to, display_name=to)
        db.add(conversation)
        db.flush()

    wamid = None
    if result.get("messages"):
        wamid = result["messages"][0].get("id")
    now = datetime.now(timezone.utc)
    db.add(
        Message(
            conversation_id=conversation.id,
            wamid=wamid,
            direction="outbound",
            message_type="text",
            body=body,
            status="sent",
            timestamp=now,
            raw_payload=json.dumps(result),
        )
    )
    conversation.last_message_at = now
    db.commit()
    return RedirectResponse(url=f"/?conversation={conversation.id}", status_code=303)


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
