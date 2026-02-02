"""Blueprint exposing the Hands-on Lab player view and generator."""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Mapping

from flask import Blueprint, jsonify, render_template, request

import db
from config import API_REQUEST_DELAY
from openai_api import analyze_certif, generate_lab_blueprint


hol_bp = Blueprint("hol", __name__)


@hol_bp.route("/hands-on-labs")
def player() -> str:
    """Render the immersive Hands-on Lab player."""

    return render_template("player.html")


@hol_bp.route("/labs-generator")
def labs_generator() -> str:
    """Render the Labs Generator control room."""

    providers = db.get_providers()
    return render_template("labs_generator.html", providers=providers)


def _flatten_analysis(
    analysis: Mapping[str, str] | Iterable[Dict[str, str]],
) -> Dict[str, str]:
    if isinstance(analysis, Mapping):
        return {key: str(value) for key, value in analysis.items()}
    flattened: Dict[str, str] = {}
    for entry in analysis:
        for key, value in entry.items():
            flattened[key] = str(value)
    return flattened


def _map_step_types(analysis: Dict[str, str]) -> Dict[str, str]:
    scenario_to_steps = {
        "case": ["quiz", "inspect_file"],
        "archi": ["architecture"],
        "config": ["terminal", "console_form"],
        "console": ["terminal", "console_form"],
        "code": ["terminal", "inspect_file"],
    }
    step_flags = {key: "0" for key in ["quiz", "architecture", "terminal", "console_form", "inspect_file"]}
    for scenario, steps in scenario_to_steps.items():
        if analysis.get(scenario) == "1":
            for step in steps:
                step_flags[step] = "1"
    if not any(flag == "1" for flag in step_flags.values()):
        # Always allow quiz as fallback so the prompt stays consistent.
        step_flags["quiz"] = "1"
    return step_flags


def _select_domains(domains: List[Dict[str, str]]) -> tuple[Dict[str, str], Dict[str, str] | None]:
    primary = random.choice(domains)
    remaining = [d for d in domains if d["id"] != primary["id"]]
    secondary = random.choice(remaining) if remaining else None
    return primary, secondary


def _build_domain_context(primary: Dict[str, str], secondary: Dict[str, str] | None) -> tuple[list[str], str]:
    names = [primary["name"]]
    descr_parts = []
    if primary.get("descr"):
        descr_parts.append(f"{primary['name']}: {primary['descr']}")
    if secondary:
        names.append(secondary["name"])
        secondary_descr = secondary.get("descr")
        if secondary_descr:
            descr_parts.append(f"{secondary['name']}: {secondary_descr}")
    description = "\n\n".join(part.strip() for part in descr_parts if part)
    return names, description or primary.get("descr", "")


def _estimate_duration(min_steps: int) -> int:
    return max(30, min_steps * 8)


def _generate_single_lab(
    index: int,
    provider: str,
    certification: str,
    difficulty: str,
    min_steps: int,
    allowed_step_types: List[str],
    domains: List[Dict[str, str]],
) -> Dict[str, object]:
    primary, secondary = _select_domains(domains)
    domain_names, domain_descr = _build_domain_context(primary, secondary)
    duration_minutes = _estimate_duration(min_steps)
    lab_payload = generate_lab_blueprint(
        provider=provider,
        certification=certification,
        domains=domain_names,
        domain_descr=domain_descr,
        difficulty=difficulty,
        min_steps=min_steps,
        step_types=allowed_step_types,
        duration_minutes=duration_minutes,
    )
    if not isinstance(lab_payload, dict):
        raise TypeError("Réponse inattendue lors de la génération du lab.")
    if API_REQUEST_DELAY:
        time.sleep(API_REQUEST_DELAY)
    lab_object = lab_payload.setdefault("lab", {})
    lab_object.setdefault("metadata", {})
    lab_object["metadata"].update(
        {
            "domains": {
                "primary": {"id": primary["id"], "name": primary["name"]},
                "secondary": (
                    {"id": secondary["id"], "name": secondary["name"]}
                    if secondary
                    else None
                ),
            }
        }
    )
    return {
        "index": index,
        "domains": {
            "primary": primary,
            "secondary": secondary,
        },
        "lab": lab_payload,
    }


@hol_bp.route("/labs-generator/api/providers")
def labs_providers():
    providers = [
        {"id": provider_id, "name": name}
        for provider_id, name in db.get_providers()
    ]
    return jsonify(providers)


@hol_bp.route("/labs-generator/api/certifications/<int:provider_id>")
def labs_certifications(provider_id: int):
    certifications = [
        {"id": cert_id, "name": name}
        for cert_id, name in db.get_certifications_by_provider(provider_id)
    ]
    return jsonify(certifications)


@hol_bp.route("/labs-generator/api/domains/<int:cert_id>")
def labs_domains(cert_id: int):
    domains = db.get_domains_description_by_certif(cert_id)
    return jsonify(domains)


@hol_bp.route("/labs-generator/api/generate", methods=["POST"])
def labs_generate():
    payload = request.get_json(force=True) or {}
    provider_id = payload.get("provider_id")
    cert_id = payload.get("cert_id")
    difficulty = (payload.get("difficulty") or "medium").strip().lower()
    lab_count = int(payload.get("lab_count") or 4)
    min_steps = max(1, int(payload.get("min_steps") or 5))

    try:
        provider_id = int(provider_id)
        cert_id = int(cert_id)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Paramètres provider_id ou cert_id invalides."}), 400

    providers = {pid: name for pid, name in db.get_providers()}
    provider_name = providers.get(provider_id)
    if not provider_name:
        return jsonify({"status": "error", "message": "Provider introuvable."}), 404

    certifications = {
        cid: name for cid, name in db.get_certifications_by_provider(provider_id)
    }
    certification_name = certifications.get(cert_id)
    if not certification_name:
        return jsonify({"status": "error", "message": "Certification introuvable pour ce provider."}), 404

    domains = db.get_domains_description_by_certif(cert_id)
    if not domains:
        return jsonify({"status": "error", "message": "Aucun domaine disponible pour cette certification."}), 400

    try:
        analysis_raw = analyze_certif(provider_name, certification_name)
    except Exception as exc:  # pragma: no cover - API failure path
        return jsonify({"status": "error", "message": str(exc)}), 502

    analysis_map = _flatten_analysis(analysis_raw)
    step_flags = _map_step_types(analysis_map)
    allowed_steps = [step for step, flag in step_flags.items() if flag == "1"]

    results: List[Dict[str, object]] = []
    errors: List[Dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=min(4, max(lab_count, 1))) as executor:
        futures = {
            executor.submit(
                _generate_single_lab,
                index,
                provider_name,
                certification_name,
                difficulty,
                min_steps,
                allowed_steps,
                domains,
            ): index
            for index in range(lab_count)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # pragma: no cover - API failure path
                errors.append({"index": index, "message": str(exc)})

    status = "ok" if not errors else "partial"
    return jsonify(
        {
            "status": status,
            "analysis": analysis_map,
            "analysis_matrix": analysis_raw,
            "step_matrix": [{key: value} for key, value in step_flags.items()],
            "step_types": allowed_steps,
            "labs": results,
            "errors": errors,
        }
    )


__all__ = ["hol_bp"]
