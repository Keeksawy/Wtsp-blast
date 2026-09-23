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

    # Warm-up ramp: max unique sends per calendar day for a number, based on how many
    # days it has been connected/active in this tool.
    #
    # [INDUSTRY OBSERVATION, not Meta-published] Anchored to the two milestones that
    # independent WhatsApp-automation sources consistently agree on: numbers become
    # "significantly more resistant to restriction" around day ~10, and get treated
    # as established/trusted around day ~25-30. Everything else is our own
    # conservative interpolation. Full ramp to steady state takes ~90 days.
    "warmup_ramp": [
        {"min_days_active": 0, "daily_cap": 8},
        {"min_days_active": 3, "daily_cap": 15},
        {"min_days_active": 7, "daily_cap": 25},
        {"min_days_active": 10, "daily_cap": 35},
        {"min_days_active": 14, "daily_cap": 45},
        {"min_days_active": 21, "daily_cap": 60},
        {"min_days_active": 30, "daily_cap": 90},
        {"min_days_active": 45, "daily_cap": 140},
        {"min_days_active": 60, "daily_cap": 200},
        {"min_days_active": 75, "daily_cap": 260},
        {"min_days_active": 90, "daily_cap": 300},
    ],
    "steady_state_daily_cap_default": 300,

    # Random delay range between individual sends on the same number (seconds).
    # (JS version uses milliseconds; Python version uses seconds throughout.)
    "jitter_delay_seconds_range": [30, 100],

    "per_contact_frequency_cap_days": 30,
    "max_follow_ups": 1,

    "opt_out_keywords": [
        "stop", "unsubscribe", "remove me", "opt out", "optout",
        "لا ترسل", "الغاء الاشتراك", "توقف", "ايقاف",
    ],

    "auto_pause": {
        "min_sends_before_evaluating": 15,
        "max_opt_out_rate": 0.08,
        "max_undelivered_rate": 0.15,
    },

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
