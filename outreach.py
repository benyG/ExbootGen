"""The trainer outreach sequence: what to send, and when to stop.

Sixty trainers at twenty seats each is the revenue goal, and sixty is a list of
names rather than a traffic problem. Each name gets a playable test generated on
the certification they teach — a gift, not a pitch — followed by two reminders
and then silence. The link carries the prospect identifier, so an account opened
weeks later still traces back to the exact person it was sent to.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from attribution import tag_link

GIFT_CHANNEL = "outreach"
GIFT_CAMPAIGN = "trainer-prospecting"

# Three messages, spaced, then the prospect is left alone. A fourth message
# costs more goodwill than it has ever earned.
SEQUENCE = (
    {"step": 1, "day": 0, "label": "Le cadeau"},
    {"step": 2, "day": 3, "label": "Les résultats"},
    {"step": 3, "day": 10, "label": "La sortie"},
)

# Statuses where chasing is pointless: they answered, joined, paid, or refused.
SETTLED_STATUSES = ("replied", "signed_up", "customer", "declined")


def gift_link(url: str, prospect_id: int) -> str:
    """Tag a shareable test so a signup traces back to this exact prospect."""

    return tag_link(url, channel=GIFT_CHANNEL, campaign=GIFT_CAMPAIGN, content=prospect_id)


def build_messages(prospect: dict, gift_url: Optional[str] = None) -> list[dict]:
    """The three messages, personalised, ready to copy into an inbox."""

    name = _first_name(prospect.get("name"))
    cert = (prospect.get("course_name") or "").strip() or "votre certification"
    org = (prospect.get("organisation") or "").strip()
    link = (gift_url or prospect.get("gift_url") or "").strip() or "[lien du test]"
    where = f" chez {org}" if org else ""

    return [
        {
            **SEQUENCE[0],
            "subject": f"Un test {cert} prêt à jouer pour vos stagiaires",
            "body": (
                f"Bonjour {name},\n\n"
                f"Je prépare des tests d'entraînement {cert} et j'en ai généré un pour vous : "
                f"10 questions, 20 minutes, jouable sans créer de compte.\n\n"
                f"{link}\n\n"
                f"Passez-le à vos stagiaires{where} si vous le trouvez utile — c'est à vous, "
                f"il n'y a rien à installer et rien à payer.\n\n"
                f"Dites-moi simplement ce qui cloche dans les questions, c'est ce qui m'aide le plus.\n"
            ),
        },
        {
            **SEQUENCE[1],
            "subject": f"Voir les scores de vos stagiaires sur {cert}",
            "body": (
                f"Bonjour {name},\n\n"
                f"Petit complément au test {cert} que je vous ai envoyé : sur ExamBoot vous pouvez "
                f"ouvrir une classe, y inviter vos stagiaires et suivre leurs scores question par "
                f"question — qui bloque sur quel domaine, avant l'examen.\n\n"
                f"Vos cours et vos questions restent les vôtres : votre espace est privé, séparé du "
                f"catalogue public.\n\n"
                f"Vous payez au siège, donc vous ne payez que les stagiaires que vous inscrivez "
                f"réellement.\n\n"
                f"Si ça vous parle, je vous ouvre l'accès et je m'occupe de la mise en place.\n"
            ),
        },
        {
            **SEQUENCE[2],
            "subject": "Je referme",
            "body": (
                f"Bonjour {name},\n\n"
                f"Sans retour de votre part je ne vous relance plus — le test {cert} reste "
                f"utilisable, gardez-le.\n\n"
                f"Si un jour vous voulez suivre une promo de stagiaires, écrivez-moi, "
                f"ça prend dix minutes à mettre en place.\n"
            ),
        },
    ]


def next_follow_up(prospect: dict, today: Optional[date] = None) -> Optional[dict]:
    """The next message due for this prospect, or ``None`` if nothing is owed.

    Returns the sequence entry with a ``due`` date and an ``overdue`` flag, so
    the console can sort the pipeline by what actually needs doing today.
    """

    if (prospect.get("status") or "") in SETTLED_STATUSES:
        return None

    sent = int(prospect.get("follow_ups") or 0)
    if sent >= len(SEQUENCE):
        return None

    today = today or date.today()
    entry = SEQUENCE[sent]

    started = _as_date(prospect.get("gift_sent_at"))
    if started is None:
        # Nothing sent yet: the gift is due now.
        return {**entry, "due": today, "overdue": True}

    due = started + timedelta(days=entry["day"])

    return {**entry, "due": due, "overdue": due <= today}


def _first_name(name: Optional[str]) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return "bonjour"

    return cleaned.split()[0]


def _as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace(" ", "T")).date()
        except ValueError:
            return None

    return None
