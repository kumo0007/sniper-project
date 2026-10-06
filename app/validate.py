import re
from datetime import datetime, timezone

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
PRIORITIES = {"3c", "og", "normal"}


def clean_username(raw: str) -> str:
    username = (raw or "").strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Usernames are 3–16 characters and may use letters, numbers, and underscores.")
    return username


def resolve_priority(username: str, requested: str | None) -> str:
    if requested in (None, "", "auto"):
        return "3c" if len(username) == 3 else "normal"
    if requested not in PRIORITIES:
        raise ValueError("Priority must be 3c, og, normal, or auto.")
    if requested == "3c" and len(username) != 3:
        raise ValueError("3-character priority only applies to 3-character names.")
    return requested


def parse_hint(raw: str | None) -> datetime | None:
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Release hint must be an ISO date and time.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
