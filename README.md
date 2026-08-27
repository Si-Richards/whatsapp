# WhatsApp POC

Proof-of-concept shared WhatsApp inbox using Meta's WhatsApp Cloud API directly.

## Features

- FastAPI backend
- Meta WhatsApp Cloud API outbound text messages
- Meta webhook verification
- `X-Hub-Signature-256` webhook validation using the Meta App Secret
- Incoming text-message persistence
- Incoming image, document, audio, video and sticker download
- Local media persistence under the Docker data volume
- Automatic read acknowledgements for inbound WhatsApp messages
- Sent/delivered/read status updates for outbound messages
- Idempotency using WhatsApp message IDs (`wamid`)
- SQLite conversation/message store
- Browser conversation inbox
- Docker Compose deployment
- No credentials committed to Git

## Requirements

You need a Meta developer app with WhatsApp configured and the following values:

- Meta App ID
- Meta App Secret
- WhatsApp access token
- WhatsApp Phone Number ID
- WhatsApp Business Account ID (WABA ID)
- A verify token that you choose yourself

## Quick start

```bash
git clone https://github.com/Si-Richards/whatsapp.git
cd whatsapp
cp .env.example .env
nano .env
```

Populate `.env` with your Meta values. Do **not** commit `.env`.

Then run:

```bash
docker compose up -d --build
```

Check the application:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok","service":"VoiceHost WhatsApp POC"}
```

Open the inbox at:

```text
http://SERVER-IP:8000/
```

## Meta webhook configuration

The application exposes:

```text
GET  /webhook   Meta webhook verification
POST /webhook   Meta webhook events
```

Meta needs a publicly reachable HTTPS callback, for example:

```text
https://whatsapp.example.com/webhook
```

In the Meta developer console configure the callback URL and set the Verify Token to exactly the value of `WHATSAPP_VERIFY_TOKEN` in `.env`.

Subscribe the WhatsApp webhook to the `messages` field. This carries inbound messages and message status notifications used by the POC.

The POST endpoint validates Meta's `X-Hub-Signature-256` HMAC using `META_APP_SECRET`. An invalid or missing signature is rejected.

## Sending messages

The UI sends text messages through the backend. Credentials never reach browser JavaScript.

A free-form text reply is normally usable while the applicable WhatsApp customer-service conversation window is open. Starting/restarting business conversations outside that window normally requires an approved WhatsApp message template. Template sending is intentionally not included in this first POC yet.

## Receiving media

For incoming `image`, `document`, `audio`, `video` and `sticker` messages, the application resolves Meta's media ID, downloads the media using the configured WhatsApp access token and stores it under:

```text
data/media/
```

Docker Compose mounts `/app/data` in the persistent `whatsapp-data` volume, so downloaded media survives container recreation.

The inbox renders images and stickers inline, provides audio/video players and provides a download link for documents.

Media received before this feature was added remains stored in the database as the original placeholder and is not downloaded retrospectively.

## Read receipts

After an inbound message has been safely persisted, the application sends a WhatsApp `status: read` acknowledgement for the message ID. This tells WhatsApp that the message has been read by the VoiceHost application.

Status webhooks for outbound messages continue to update the stored message state (`sent`, `delivered`, `read`) and are displayed in the inbox.

## Access token

The temporary access token shown in Meta's API setup page is useful for initial testing but should not be treated as a production credential. Move to the appropriate long-lived/system-user token setup before production use.

## Development without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Current POC limitations

This is intentionally not production-ready yet. It currently has:

- one WhatsApp business number
- no authentication
- no multi-tenancy
- no agent assignment/queues
- no approved-template UI
- no outbound media upload UI
- no PostgreSQL migration layer
- media downloads are performed inline during webhook processing

These are logical next phases after proving end-to-end Meta connectivity.

## Security

Never commit `.env`, access tokens or Meta App Secrets. `.env` and local database files are excluded by `.gitignore`.
