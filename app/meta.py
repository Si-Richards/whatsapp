import hashlib
import hmac
import mimetypes
import re
from pathlib import Path

import httpx

from app.config import settings


MEDIA_DIR = Path("data/media")


class MetaAPIError(RuntimeError):
    pass


def verify_signature(body: bytes, signature_header: str | None) -> bool:
    if not settings.meta_app_secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        settings.meta_app_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    supplied = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, supplied)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.whatsapp_access_token}"}


def _messages_url() -> str:
    return (
        f"https://graph.facebook.com/{settings.meta_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )


def _media_upload_url() -> str:
    return (
        f"https://graph.facebook.com/{settings.meta_api_version}/"
        f"{settings.whatsapp_phone_number_id}/media"
    )


async def send_text(to: str, body: str, reply_to: str | None = None) -> dict:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise MetaAPIError("WhatsApp credentials are not configured")

    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    if reply_to:
        payload["context"] = {"message_id": reply_to}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _messages_url(),
            headers={**_headers(), "Content-Type": "application/json"},
            json=payload,
        )

    if response.is_error:
        raise MetaAPIError(f"Meta API {response.status_code}: {response.text}")
    return response.json()


async def send_media(
    to: str,
    content: bytes,
    filename: str,
    mime_type: str,
    message_type: str,
    caption: str | None = None,
    reply_to: str | None = None,
) -> dict:
    """Upload media to Meta then send it to the recipient."""
    if message_type not in {"image", "document", "audio", "video"}:
        raise MetaAPIError(f"Unsupported outbound media type: {message_type}")
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise MetaAPIError("WhatsApp credentials are not configured")

    async with httpx.AsyncClient(timeout=90.0) as client:
        upload = await client.post(
            _media_upload_url(),
            headers=_headers(),
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": (filename, content, mime_type)},
        )
        if upload.is_error:
            raise MetaAPIError(f"Meta media upload {upload.status_code}: {upload.text}")

        media_id = upload.json().get("id")
        if not media_id:
            raise MetaAPIError("Meta media upload did not return a media ID")

        media_payload: dict = {"id": media_id}
        if caption and message_type in {"image", "document", "video"}:
            media_payload["caption"] = caption
        if message_type == "document":
            media_payload["filename"] = filename

        payload: dict = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": message_type,
            message_type: media_payload,
        }
        if reply_to:
            payload["context"] = {"message_id": reply_to}

        sent = await client.post(
            _messages_url(),
            headers={**_headers(), "Content-Type": "application/json"},
            json=payload,
        )
        if sent.is_error:
            raise MetaAPIError(f"Meta media send {sent.status_code}: {sent.text}")

    result = sent.json()
    result["uploaded_media_id"] = media_id
    return result


async def mark_message_read(message_id: str) -> dict:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise MetaAPIError("WhatsApp credentials are not configured")

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _messages_url(),
            headers={**_headers(), "Content-Type": "application/json"},
            json=payload,
        )

    if response.is_error:
        raise MetaAPIError(
            f"Meta read acknowledgement {response.status_code}: {response.text}"
        )
    return response.json()


def _safe_filename(value: str) -> str:
    value = Path(value).name
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value[:180] or "attachment"


def safe_filename(value: str) -> str:
    return _safe_filename(value)


async def download_media(media_id: str, preferred_filename: str | None = None) -> dict:
    if not settings.whatsapp_access_token:
        raise MetaAPIError("WhatsApp access token is not configured")

    metadata_url = f"https://graph.facebook.com/{settings.meta_api_version}/{media_id}"

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        metadata_response = await client.get(metadata_url, headers=_headers())
        if metadata_response.is_error:
            raise MetaAPIError(
                f"Meta media metadata {metadata_response.status_code}: "
                f"{metadata_response.text}"
            )

        metadata = metadata_response.json()
        download_url = metadata.get("url")
        if not download_url:
            raise MetaAPIError("Meta media response did not contain a download URL")

        media_response = await client.get(download_url, headers=_headers())
        if media_response.is_error:
            raise MetaAPIError(
                f"Meta media download {media_response.status_code}: "
                f"{media_response.text}"
            )

    mime_type = (
        metadata.get("mime_type")
        or media_response.headers.get("content-type", "application/octet-stream")
    ).split(";", 1)[0]

    if preferred_filename:
        original = _safe_filename(preferred_filename)
        filename = f"{media_id}_{original}"
    else:
        extension = mimetypes.guess_extension(mime_type) or ""
        if extension == ".jpe":
            extension = ".jpg"
        filename = f"{media_id}{extension}"

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    path = MEDIA_DIR / filename
    path.write_bytes(media_response.content)

    return {
        "media_id": media_id,
        "mime_type": mime_type,
        "filename": filename,
        "url": f"/media/{filename}",
        "size": len(media_response.content),
        "sha256": metadata.get("sha256"),
    }
