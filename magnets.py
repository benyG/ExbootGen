"""Gated assets: turning a generated carousel into an address.

Publishing a carousel gives the content away and captures nothing. The same
PDF, hosted behind a one-field form on the platform, trades it for an email —
and the landing page then sends the visitor on to a free test. The console
generates the asset; the platform hosts it, because the platform is what the
public can reach.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

import requests

from attribution import tag_link
from config import EXAMBOOT_API_KEY, EXAMBOOT_MAGNET_URL

#: Assets are slide decks and short guides; the platform refuses anything larger.
MAX_FILE_BYTES = 10 * 1024 * 1024

DEFAULT_CAMPAIGN = "lead-magnet"


class MagnetPublicationError(RuntimeError):
    """Raised when the platform refuses or cannot receive an asset."""


def push_magnet(
    title: str,
    promise: str,
    *,
    bullets: Optional[str] = None,
    locale: str = "en",
    course: Optional[int] = None,
    pdf_path: Optional[Path] = None,
    slug: Optional[str] = None,
    pub: bool = True,
    timeout: int = 30,
) -> dict:
    """Publish one asset on the platform and return its slug and landing URL.

    Sending the same slug again replaces the asset in place, so a corrected PDF
    keeps its URL and the leads already attached to it.
    """

    if not EXAMBOOT_API_KEY:
        raise MagnetPublicationError(
            "La clé API Examboot est manquante. Configurez la variable d'environnement API_KEY."
        )

    title = (title or "").strip()
    promise = (promise or "").strip()
    if not title or not promise:
        raise MagnetPublicationError("Un aimant à prospects a besoin d'un titre et d'une promesse.")

    payload: dict = {
        "title": title,
        "promise": promise,
        "locale": locale,
        "pub": pub,
    }
    if bullets:
        payload["bullets"] = bullets
    if course:
        payload["course"] = int(course)
    if slug:
        payload["slug"] = slug
    if pdf_path is not None:
        payload["file"] = _encode_pdf(Path(pdf_path))

    headers = {
        "Authorization": f"Bearer {EXAMBOOT_API_KEY}",
        "x-api-key": EXAMBOOT_API_KEY,
        "Accept": "application/json",
    }

    try:
        response = requests.post(EXAMBOOT_MAGNET_URL, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:  # pragma: no cover - network errors
        raise MagnetPublicationError("Impossible de contacter la plateforme Examboot.") from exc

    if response.status_code >= 400:
        raise MagnetPublicationError(
            f"La plateforme a refusé l'aimant à prospects ({response.status_code})."
        )

    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover - invalid JSON
        raise MagnetPublicationError("Réponse invalide reçue depuis la plateforme.") from exc

    if not isinstance(data, dict) or not data.get("url"):
        raise MagnetPublicationError("La réponse de la plateforme ne contient pas d'URL.")

    return data


def magnet_link(url: str, *, channel: str, campaign: Optional[str] = None, content: Optional[str] = None) -> str:
    """Tag a landing URL so the dashboard can tell which asset earned what."""

    return tag_link(url, channel=channel, campaign=campaign or DEFAULT_CAMPAIGN, content=content)


def build_promise(subject: str, question: str) -> str:
    """The sentence the landing page leads with, from the carousel topic.

    Written in English because the carousel generator writes in English; the
    surrounding page is translated, the asset's own words are not.
    """

    subject = (subject or "").strip().rstrip(".")
    question = (question or "").strip()
    slides = f"Five slides on {subject}, and the reasoning behind each one." if subject else ""

    if question and slides:
        return f"{question} {slides}"

    return question or slides


def bullets_from_pages(pages: list) -> str:
    """The carousel headlines, one per line, as the landing page's checklist."""

    lines = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        headline = str(page.get("headline") or "").strip()
        if headline:
            lines.append(headline)

    return "\n".join(lines)


def _encode_pdf(path: Path) -> str:
    if not path.exists():
        raise MagnetPublicationError(f"Fichier introuvable : {path}")

    binary = path.read_bytes()

    if len(binary) > MAX_FILE_BYTES:
        raise MagnetPublicationError("Le fichier dépasse 10 Mo.")

    if not binary.startswith(b"%PDF"):
        raise MagnetPublicationError("Seuls les fichiers PDF sont acceptés.")

    return base64.b64encode(binary).decode("ascii")
