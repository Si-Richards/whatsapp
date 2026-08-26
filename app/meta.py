import hashlib
import hmac

import httpx

from app.config import settings


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


async def send_text(to: str, body: str) -> dict:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise MetaAPIError("WhatsApp credentials are not configured")

    url = (
        f"https://graph.facebook.com/{settings.meta_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.is_error:
        raise MetaAPIError(f"Meta API {response.status_code}: {response.text}")
    return response.json()
