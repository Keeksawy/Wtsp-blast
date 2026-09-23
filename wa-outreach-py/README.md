# WhatsApp Owner Outreach (Python) — Safety-First Bulk Sender

This is a Python port of the original Node.js `wa-outreach` tool — same safety logic,
same dashboard, same design principles. Requested as a rewrite; **the Node version
is the more battle-tested option** (see "Why the Node version is safer to rely on"
below) — this exists because Python was explicitly preferred.

**Read this first.** This automates WhatsApp Web by driving the real
web.whatsapp.com page with [Playwright](https://playwright.dev/) — an unofficial
approach. That is a violation of WhatsApp's Terms of Service regardless of how
carefully it's used — the safety logic in this tool reduces the *behavioural*
signals associated with restrictions (bursty sending, identical text, no warm-up,
ignoring opt-outs), it does not make the automation itself sanctioned. For
sustained volume, migrate to the official WhatsApp Business Platform. Use this tool
at small-to-moderate scale, on numbers you're prepared to lose.

Nothing in this tool does, or ever will, spoof device fingerprints, rotate
IPs/proxies, use multiple SIMs/emulators, or fabricate accounts. It only automates
real WhatsApp Web sessions you personally connect via QR code, the same way you'd
use WhatsApp Web by hand.

## Why the Node version is safer to rely on

The Node version uses `whatsapp-web.js`, a library that talks to WhatsApp's
internal WebSocket protocol directly — sending and receiving messages is
event-driven and reasonably robust to WhatsApp shipping UI changes.

This Python version instead drives the *visible* web.whatsapp.com page with
Playwright and **polls the DOM** for QR codes, send buttons, and new messages,
using CSS selectors defined in `src/whatsapp_manager.py::SELECTORS`. WhatsApp
changes its web UI's internal class names periodically without notice. When that
happens, this version can silently break (QR detection, sending, or inbound
scraping) until someone updates `SELECTORS` to match the new DOM. The Node version
doesn't have this failure mode.

Concretely, weaker than the Node version in:
- **Inbound message / opt-out detection** — polls unread chats every ~8 seconds and
  scrapes the last message bubble's text, capped at 10 chats per poll. Under real
  load with many simultaneous replies this can lag or miss messages in ways the
  event-driven Node version won't.
- **Fragility to WhatsApp UI changes** — see above.
- **Send confirmation** — detects an "invalid number" dialog but has less robust
  delivery-status detection than whatsapp-web.js's native message-ack events.

If reliability matters more than the language, use the Node version. If you hit a
selector-related bug here, the fix is almost always: open the WhatsApp Web page in
a real Chrome browser, inspect the relevant element, and update `SELECTORS`.

## What's in the box

Functionally identical to the Node version:

- Multi-number support (one Playwright Chromium profile per number, persistent
  login — scan QR once).
- Warm-up ramp, business-hours gating, randomised delays (`config/defaults.py`).
- One owner → one number → one agent, locked.
- 5 initial templates + 3 follow-ups, plus a content-variance layer (greeting +
  opt-out phrasing from small pools, deterministic per contact) — see "On
  reply-rate monitoring / content variance" below.
- Reply-rate monitoring with auto-pause on near-zero engagement.
- Automatic opt-out detection + confirmation.
- Per-contact frequency cap, auto-pause on opt-out/failure rate.
- Excel/CSV import (`openpyxl` + `phonenumbers`), de-duplication, multi-unit
  linking.
- Same dashboard — `public/` is reused **unchanged** from the Node version; this
  backend mirrors its API exactly.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium      # downloads the browser Playwright drives
cp .env.example .env             # edit COMPANY_NAME / DEFAULT_AGENT_NAME
pytest test/test_all.py -q       # runs the safety/contacts/templates unit tests
python app.py
```

Open http://localhost:3000.

## Using it

Identical flow to the Node version:

1. **Add a number** — "+ Add Number", scan the QR with that number's phone
   (WhatsApp → Settings → Linked Devices → Link a Device).
2. **Import owners** — Excel/CSV with Owner Name, Mobile Number, Unit Number
   columns.
3. **Assign & launch** — tick number(s), "Assign Contacts," then "Start Sending."
4. **Watch the dashboard** — sends, replies, reply-rate status, opt-outs.
5. **Handle replies** — Inbox panel; log outcomes via
   `POST /api/contacts/:contactId/outcome`.

## Tuning safety behaviour

Same knobs, now in `config/defaults.py` (snake_case keys, otherwise identical
values/reasoning to the Node version's `config/defaults.js`):

- `business_hours`, `warmup_ramp`, `steady_state_daily_cap_default`
- `jitter_delay_seconds_range` (seconds, not milliseconds, in this version)
- `per_contact_frequency_cap_days`, `max_follow_ups`
- `opt_out_keywords`
- `auto_pause` — opt-out-rate / failure-rate thresholds
- `reply_monitoring` — grace period, sample size, healthy/pause reply-rate
  thresholds
- `content_variance.enabled`

### On reply-rate monitoring / content variance

Same policy as the Node version, repeated here because it matters: reply-rate
monitoring must only ever measure genuine recipient engagement. **Do not** have
numbers message or auto-reply to each other, to test contacts, or to anything that
isn't a real owner who received the outreach — that fabricates the exact evidence
this check exists to require, and a cluster of numbers that only ever talk to each
other is its own detectable pattern. The only legitimate lever is upstream: better
list quality, better opt-in, more relevant messaging. Content variance (greeting/
opt-out phrasing pools) exists to avoid sending byte-identical text to dozens of
recipients — it is phrasing variance for a genuinely honest message, not
obfuscation of intent.

## Exposing it via a public link

`python app.py` only listens on your machine (`localhost:3000`). To get a
shareable `https://` link without deploying to a server, run a quick tunnel
(e.g. Cloudflare Tunnel) pointed at `http://localhost:3000` — your computer still
needs to stay on and connected for the link to keep working, since the app and all
its WhatsApp sessions run locally.

## What's not built yet

Same list as the Node version, plus:

- A more robust inbound-message pipeline (see "Why the Node version is safer to
  rely on"). If you're hitting missed-reply issues, start there.
- Delivery/read-receipt status (sent-vs-delivered-vs-read) isn't distinguished —
  everything Playwright successfully clicked "Send" on is logged as `sent`.

## If a number gets restricted anyway

Same procedure as the Node version: pause it immediately, don't reflexively move
the same list to another number, use WhatsApp's own appeal process, lean on
SMS/calls/email for owners who still need reaching while it's under review.
