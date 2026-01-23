# Étape 2 — Service MCP: certifications non publiées

Cette étape expose un endpoint API dédié à MCP pour récupérer les certifications non publiées, avec les métriques liées au domaine par défaut (total de questions, questions dans le domaine default, etc.).

## Endpoint

- `GET /api/mcp/unpublished-certifications`
  - Source: `db.get_unpublished_certifications_report()`
  - Retourne une liste de certifications non publiées groupées par provider (via la logique DB existante).
  - Le champ `automation_eligible` est vrai quand `pub = 2`.
  - Objectif MCP: alimenter la sélection automatique de certifications à traiter (workflow étape 1).

## Source de données

- `courses.pub` est utilisé pour filtrer les certifications non publiées (`pub != 1`).
- La valeur `pub = 2` indique l’éligibilité au traitement automatique.
- `modules.code_cert` et `courses.descr2` servent à calculer le domaine default.

## Références code

- Route MCP: `app.py` (ajout de `/api/mcp/unpublished-certifications`).
- Requête DB: `db.get_unpublished_certifications_report()`.
