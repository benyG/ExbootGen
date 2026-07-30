"""Reaching the admin when the console needs a human.

Tokens expire without warning and a feed can refuse a post for reasons no
retry will fix. When that happens the console stops guessing and writes: the
alert is always visible in its own interface, and an email goes out on top of
it so nobody has to be watching the screen at the time.

Sending is best effort by design. A console that crashes because its SMTP
server is down would turn a publication problem into an outage.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Optional

from config import (
    ADMIN_EMAIL,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    SMTP_USE_TLS,
)


def notifications_configured() -> bool:
    """Whether an email can actually leave this console."""

    return bool(SMTP_HOST and ADMIN_EMAIL and (SMTP_FROM or SMTP_USER))


def notify_admin(subject: str, body: str, sender=None) -> bool:
    """Email the admin. Returns whether it left, never raises."""

    if not notifications_configured():
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM or SMTP_USER
    message["To"] = ADMIN_EMAIL
    message.set_content(body)

    try:
        (sender or _send)(message)
    except Exception:  # pragma: no cover - any SMTP failure is non-fatal
        return False

    return True


def build_failure_notice(publications: list, console_url: str = "") -> tuple[str, str]:
    """The subject and body describing what is waiting for the admin."""

    expired = [row for row in publications if row.get("status") == "needs_token"]
    failed = [row for row in publications if row.get("status") == "failed"]

    if expired:
        subject = f"ExamBoot : reconnectez {_channels(expired)} — {len(publications)} vidéo(s) en attente"
    else:
        subject = f"ExamBoot : {len(publications)} publication(s) vidéo à reprendre"

    lines = []
    if expired:
        lines.append(
            "Un jeton a expiré. Tant qu'il n'est pas renouvelé, ces publications "
            "restent en attente :"
        )
        lines.extend(_describe(row) for row in expired)
        lines.append("")

    if failed:
        lines.append("Ces publications ont échoué après plusieurs tentatives :")
        lines.extend(_describe(row) for row in failed)
        lines.append("")

    lines.append(
        "Les clips sont prêts et téléchargeables : vous pouvez les publier à la "
        "main puis les marquer comme publiés, ou reconnecter le compte et relancer."
    )
    if console_url:
        lines.append("")
        lines.append(console_url)

    return subject, "\n".join(lines)


def _describe(row: dict) -> str:
    error = (row.get("last_error") or "").strip().splitlines()
    detail = error[0][:200] if error else "raison inconnue"

    return f"- {row.get('channel')} · {row.get('file')} : {detail}"


def _channels(rows: list) -> str:
    seen = []
    for row in rows:
        channel = row.get("channel")
        if channel and channel not in seen:
            seen.append(channel)

    return ", ".join(seen) or "le compte"


def _send(message: EmailMessage) -> None:  # pragma: no cover - real network
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message)
