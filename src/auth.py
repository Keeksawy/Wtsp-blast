"""
Multi-user authentication for the dashboard.
- Only @drivenproperties.com email addresses are accepted.
- Passwords are hashed with PBKDF2-SHA256 + a per-user random salt (stdlib only, no extra deps).
- Sessions are random 32-byte tokens stored in the DB with a 7-day expiry.
"""
import binascii
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone, timedelta

ALLOWED_DOMAIN = "drivenproperties.com"
SESSION_TTL_DAYS = 7


def is_allowed_email(email: str) -> bool:
    return isinstance(email, str) and email.strip().lower().endswith(f"@{ALLOWED_DOMAIN}")


def hash_password(password: str) -> str:
    """Return a storable 'salt:key' string."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return binascii.hexlify(salt).decode() + ":" + binascii.hexlify(key).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":")
        salt = binascii.unhexlify(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(binascii.hexlify(key).decode(), key_hex)
    except Exception:
        return False


def generate_token() -> str:
    return secrets.token_hex(32)


def token_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()


def is_token_expired(expiry_iso: str) -> bool:
    try:
        expiry = datetime.fromisoformat(expiry_iso)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expiry
    except Exception:
        return True
