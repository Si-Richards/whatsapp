# VoiceHost WhatsApp POC

Proof-of-concept shared WhatsApp inbox using Meta's WhatsApp Cloud API directly.

## Phase 2 features

The current build adds a more complete shared-agent workflow on top of the original two-way POC:

- live inbox refresh using Server-Sent Events
- conversation search across name, WhatsApp number and message body
- Open / Unread / Archived / All filters
- outbound text, image, document, audio and video sending
- inbound image, document, audio, video and sticker handling
- reply-to-message support using WhatsApp message context
- sent / delivered / read status display
- automatic read acknowledgements for inbound messages
- basic agents and conversation assignment
- internal notes that are never sent to WhatsApp
- archive / restore conversation workflow
- new inbound activity automatically reopens an archived conversation
- persistent media storage in the Docker data volume
- lightweight migration of existing SQLite POC databases to the Phase 2 schema

## Core platform features

- FastAPI backend
- Meta WhatsApp Cloud API directly
- Meta webhook verification
- `X-Hub-Signature-256` webhook validation using the Meta App Secret
- idempotency using WhatsApp message IDs (`wamid`)
- SQLite conversation/message store
- browser shared inbox
- Docker Compose deployment
- no credentials committed to Git

## Requirements

You need a Meta developer app with WhatsApp configured and the following values:

- Meta App ID
- Meta App Secret
- WhatsApp access token
- WhatsApp Phone Number ID
- WhatsApp Business Account ID (WABA ID)
- a verify token that you choose yourself

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

Expected Phase 2 response:

```json
{"status":"ok","service":"VoiceHost WhatsApp POC","phase":2}
```

## Upgrading an existing POC install

Pull and rebuild:

```bash
cd /opt/whatsapp
git pull
docker compose down
docker compose up -d --build
docker compose logs -f
```

The application creates the new `agents` table and adds the Phase 2 conversation columns to an existing SQLite database on startup. Existing conversations and messages are retained.

## Meta webhook configuration

The application exposes:

```text
GET  /webhook   Meta webhook verification
POST /webhook   Meta webhook events
```

Meta needs a publicly reachable HTTPS callback, for example:

```text
https://devhook.example.com/webhook
```

Configure the callback in the Meta developer console and set the Verify Token to exactly the value of `WHATSAPP_VERIFY_TOKEN` in `.env`.

Subscribe the WhatsApp webhook to the `messages` field.

The POST endpoint validates Meta's `X-Hub-Signature-256` HMAC using `META_APP_SECRET`.

## Live inbox

The browser opens an SSE connection to:

```text
/events
```

The POC watches conversation and message state every two seconds. If new activity arrives and the agent is not currently composing a message, the inbox refreshes automatically. If the agent is typing or has selected an attachment, a **New activity · refresh** notice appears instead so draft text is not discarded.

When using Nginx, SSE normally works with the application's `X-Accel-Buffering: no` response header. If your reverse proxy still buffers the event stream, add this to the proxy location:

```nginx
proxy_buffering off;
proxy_cache off;
```

## Outbound media

The composer accepts an optional attachment. The backend uploads it to Meta, sends the resulting media ID through WhatsApp, and stores a local copy under:

```text
data/media/
```

The current POC limits browser uploads to 25 MB. Meta's own media-type and size limits still apply and may be lower depending on the media type.

## Reply-to-message

Hover a WhatsApp message and choose **Reply**. The composer records the original `wamid` and the outbound request includes a WhatsApp message context. Incoming contextual replies are also rendered with a quoted preview when the referenced message exists locally.

## Agents and assignment

Phase 2 introduces a simple `agents` table. Agents can be added from the conversation details panel and conversations can be assigned or returned to **Unassigned**.

This is deliberately a basic POC model: there is still no authentication or mapping between an authenticated VoiceHost portal user and an agent record.

## Internal notes

Internal notes are stored as local messages with direction `internal`. They appear in the timeline but are never sent to Meta/WhatsApp.

## Read receipts

After an inbound message has been persisted, the application sends a WhatsApp `status: read` acknowledgement for that `wamid`.

Status webhooks for outbound messages update the local state through `sent`, `delivered` and `read`.

## Current limitations

This remains a proof of concept rather than a production contact-centre platform. Important remaining work includes:

- authentication and real VoiceHost user identity
- multi-tenancy and reseller/customer isolation
- PostgreSQL and proper database migrations
- background queue/worker processing for webhooks and media
- approved WhatsApp template management and conversation-window enforcement
- multiple WhatsApp numbers/WABAs
- presence and collision prevention when several agents open the same conversation
- audit/event history for assignment and administrative changes
- rate limiting and production observability

## Security

Never commit `.env`, access tokens or Meta App Secrets. `.env` and local database files are excluded by `.gitignore`.
