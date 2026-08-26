import json
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

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.get("/", response_class=HTMLResponse)
def inbox(request: Request, conversation: int | None = None, db: Session = Depends(get_db)):
    conversations = db.scalars(
        select(Conversation).order_by(Conversation.last_message_at.desc())
    ).all()
    selected = None
    messages = []
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
    db.add(Message(
        conversation_id=conversation.id,
        wamid=wamid,
        direction="outbound",
        message_type="text",
        body=body,
        status="sent",
        timestamp=now,
        raw_payload=json.dumps(result),
    ))
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
    if not verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    process_webhook(db, payload)
    return {"status": "ok"}
