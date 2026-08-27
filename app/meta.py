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


async def send_text(to: str, body: str) -> dict:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise MetaAPIError("WhatsApp credentials are not configured")

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    headers = {
        **_headers(),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(_messages_url(), headers=headers, json=payload)

    if response.is_error:
        raise MetaAPIError(f"Meta API {response.status_code}: {response.text}")
    return response.json()


async def mark_message_read(message_id: str) -> dict:
    """Tell WhatsApp that an inbound message has been read by VoiceHost."""
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise MetaAPIError("WhatsApp credentials are not configured")

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    headers = {
        **_headers(),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(_messages_url(), headers=headers, json=payload)

    if response.is_error:
        raise MetaAPIError(
            f"Meta read acknowledgement {response.status_code}: {response.text}"
        )
    return response.json()


def _safe_filename(value: str) -> str:
    value = Path(value).name
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value[:180] or "attachment"


async def download_media(media_id: str, preferred_filename: str | None = None) -> dict:
    """Resolve Meta's temporary media URL and persist the media locally."""
    if not settings.whatsapp_access_token:
        raise MetaAPIError("WhatsApp access token is not configured")

    metadata_url = (
        f"https://graph.facebook.com/{settings.meta_api_version}/{media_id}"
    )

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
