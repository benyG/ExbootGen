"""Blueprint exposing the Hands-on Lab player view."""

from flask import Blueprint, render_template


hol_bp = Blueprint("hol", __name__)


@hol_bp.route("/hands-on-labs")
def player() -> str:
    """Render the immersive Hands-on Lab player."""

    return render_template("player.html")


__all__ = ["hol_bp"]
