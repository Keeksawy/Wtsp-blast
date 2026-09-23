# Message templates. 1:1 port of src/templates.js, including the content-variance
# engine. Variables: {{Owner_Name}} {{Unit_Number}} {{Agent_Name}} {{Company_Name}}
# {{Greeting}} {{OptOutLine}} — the last two are filled in per-contact by
# render_for_contact, not fixed text (see the content-variance note below).
import re

INITIAL_TEMPLATES = [
    {
        "id": "A",
        "label": "Direct & professional",
        "text": "{{Greeting}} {{Owner_Name}}, this is {{Agent_Name}} from {{Company_Name}} in Dubai. I'm reaching out about the property registered under unit {{Unit_Number}} in our records. Are you currently open to selling or renting it out, or is it not on your radar at the moment? {{OptOutLine}}",
    },
    {
        "id": "B",
        "label": "Question-first",
        "text": "{{Greeting}} {{Owner_Name}}, {{Agent_Name}} here from {{Company_Name}}. Quick question regarding unit {{Unit_Number}} on our books — any interest in selling or renting it in the near future? No pressure either way. {{OptOutLine}}",
    },
    {
        "id": "C",
        "label": "Market-context opener",
        "text": "{{Greeting}} {{Owner_Name}}, this is {{Agent_Name}} with {{Company_Name}}. We're seeing strong buyer/tenant demand in Dubai right now and I'm checking in with owners on our list, including unit {{Unit_Number}}. Would you be open to selling or renting, or is that not something you're considering currently? {{OptOutLine}}",
    },
    {
        "id": "D",
        "label": "Short & informal",
        "text": "{{Greeting}} {{Owner_Name}}, it's {{Agent_Name}} from {{Company_Name}}. Checking in about unit {{Unit_Number}} — any plans to sell or rent it out this year? Let me know either way. {{OptOutLine}}",
    },
    {
        "id": "E",
        "label": "Courtesy-led",
        "text": "{{Greeting}} {{Owner_Name}}, my name is {{Agent_Name}}, I work with {{Company_Name}} here in Dubai. I hope this isn't an inconvenience — I wanted to ask whether unit {{Unit_Number}} is something you'd consider selling or renting at the moment. Feel free to say no. {{OptOutLine}}",
    },
]

FOLLOWUP_TEMPLATES = [
    {
        "id": "F1",
        "label": "After no reply",
        "text": "{{Greeting}} {{Owner_Name}}, following up briefly — {{Agent_Name}} from {{Company_Name}} here regarding unit {{Unit_Number}}. No worries if it's not relevant right now, just wanted to check before I close this out on our end. {{OptOutLine}}",
    },
    {
        "id": "F2",
        "label": "After a maybe/not-right-now reply",
        "text": "Thanks for letting me know, {{Owner_Name}}. If your plans for unit {{Unit_Number}} change down the line, feel free to reach out — I'll check back in a few months unless you'd prefer I didn't. {{OptOutLine}}",
    },
    {
        "id": "F3",
        "label": "Reconnecting after a longer gap",
        "text": "{{Greeting}} {{Owner_Name}}, {{Agent_Name}} from {{Company_Name}} again — it's been a while since we last spoke about unit {{Unit_Number}}. Has anything changed on your end regarding selling or renting? {{OptOutLine}}",
    },
]

OPT_OUT_CONFIRMATION = {
    "id": "OPTOUT",
    "label": "Opt-out confirmation",
    "text": "Understood, {{Owner_Name}} — you won't receive further messages from {{Company_Name}} regarding unit {{Unit_Number}}. Apologies for the inconvenience, and thank you for letting us know.",
}

# Content-variance pools — see README "On reply-rate monitoring / content variance"
# for why this is phrasing variance for a genuinely human-written honest message,
# not obfuscation of intent.
GREETING_VARIANTS = ["Hi", "Hello", "Hi there", "Good day", "Hey"]

OPT_OUT_LINE_VARIANTS = [
    "Reply STOP anytime if you'd rather not be contacted.",
    "Just reply STOP if now isn't a good time.",
    "Text STOP if you'd prefer not to hear from us.",
    "Reply STOP to opt out.",
    "No problem if not — just reply STOP and I'll stop reaching out.",
]


def render(template_text, v):
    out = template_text
    out = out.replace("{{Owner_Name}}", v.get("ownerName") or "there")
    out = out.replace("{{Unit_Number}}", v.get("unitNumber") or "your unit")
    out = out.replace("{{Agent_Name}}", v.get("agentName") or "our team")
    out = out.replace("{{Company_Name}}", v.get("companyName") or "our agency")
    out = out.replace("{{Greeting}}", v.get("greeting") or "Hi")
    out = out.replace("{{OptOutLine}}", v.get("optOutLine") or OPT_OUT_LINE_VARIANTS[0])
    return out


def _hash_string(s):
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def pick_initial_variant(contact_id):
    n = _hash_string(contact_id) % len(INITIAL_TEMPLATES)
    return INITIAL_TEMPLATES[n]


def _pick_from_pool(pool, seed):
    return pool[_hash_string(seed) % len(pool)]


def get_initial_by_id(template_id):
    return next((t for t in INITIAL_TEMPLATES if t["id"] == template_id), None)


def get_followup_by_id(template_id):
    return next((t for t in FOLLOWUP_TEMPLATES if t["id"] == template_id), None)


def render_for_contact(kind, contact, v, follow_up_index=0, cfg=None):
    variance_on = not cfg or cfg.get("content_variance", {}).get("enabled", True) is not False

    if kind == "initial":
        template_obj = (
            get_initial_by_id(contact.get("templateVariant"))
            if contact.get("templateVariant") else None
        ) or pick_initial_variant(contact["contactId"])
    else:
        template_obj = FOLLOWUP_TEMPLATES[(follow_up_index or 0) % len(FOLLOWUP_TEMPLATES)]

    greeting = _pick_from_pool(GREETING_VARIANTS, contact["contactId"] + "|greet") if variance_on else GREETING_VARIANTS[0]
    opt_out_line = _pick_from_pool(OPT_OUT_LINE_VARIANTS, contact["contactId"] + "|stop") if variance_on else OPT_OUT_LINE_VARIANTS[0]

    text = render(template_obj["text"], {**v, "greeting": greeting, "optOutLine": opt_out_line})

    return {"text": text, "templateObj": template_obj, "greeting": greeting, "optOutLine": opt_out_line}
