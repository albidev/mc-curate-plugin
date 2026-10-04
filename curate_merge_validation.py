"""Strict merge inputs shared by HTTP adapters and background jobs."""
import re

from handlers import CurateIntegrationError


def field(body, key, pattern=None):
    value = body.get(key)
    if not isinstance(value, str) or not value or value != value.strip() or (pattern and not re.fullmatch(pattern, value)):
        raise CurateIntegrationError(400, "bad_request", f"Invalid or missing {key}.")
    return value


def identity(body):
    return (field(body, "candidate_id", r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"),
            field(body, "vault", r"[A-Za-z0-9_-]+"))
