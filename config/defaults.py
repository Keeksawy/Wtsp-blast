# Default safety configuration.
# These numbers are our own conservative starting points, NOT figures published
# or guaranteed by Meta/WhatsApp. Tune them based on your own delivery/block/report
# data (see the design report's "Sending Controls" section).
#
# This is a 1:1 port of config/defaults.js from the Node version — same numbers,
# same reasoning, same sourcing notes. Kept in sync deliberately.

DEFAULTS = {
    # UAE working week default: Sunday-Thursday, 9am-7pm local time.
    # Python's date.weekday(): 0=Mon,1=Tue,2=Wed,3=Thu,4=Fri,5=Sat,6=Sun
    # We store as Sun-Thu using the same 0=Sun..6=Sat convention as the JS version
    # (see safety.weekday_sun0 helper) so the two configs stay directly comparable.
    "business_hours": {
        "start_hour": 9,
        "end_hour": 19,
        "days": [0, 1, 2, 3, 4],  # 0=Sun,1=Mon,2=Tue,3=Wed,4=Thu
    },

    # Random delay range between individual sends on the same number (seconds).
    # (JS version uses milliseconds; Python version uses seconds throughout.)
    "jitter_delay_seconds_range": [30, 100],

    "per_contact_frequency_cap_days": 30,
    "max_follow_ups": 1,

    "opt_out_keywords": [
        "stop", "unsubscribe", "remove me", "opt out", "optout",
        "لا ترسل", "الغاء الاشتراك", "توقف", "ايقاف",
    ],

    # Keywords that signal the contact wants to sell or rent.
    # Detected in inbound replies — triggers a CRM webhook POST.
    "sell_keywords": [
        "sell", "selling", "sale", "i want to sell", "want to sell",
        "بيع", "يبيع", "اريد البيع",
    ],
    "rent_keywords": [
        "rent", "renting", "lease", "i want to rent", "want to rent",
        "ايجار", "يؤجر", "اريد الايجار",
    ],

    # Reply-rate monitoring — see safety.py for the "never fake this" warning.
    "reply_monitoring": {
        "grace_period_days": 3,
        "min_evaluable_contacts": 15,
        "healthy_reply_rate": 0.15,
        "pause_below_reply_rate": 0.08,
    },

    "content_variance": {
        "enabled": True,
    },
}
