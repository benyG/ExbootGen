# Étape 7 — Service MCP: génération des blueprints

Cette étape expose un endpoint MCP pour générer automatiquement les blueprints des domaines d’une certification.

## Endpoint

- `POST /blueprints/api/mcp/certifications/<cert_id>/generate-blueprints`
  - Accepte le même payload que l’endpoint standard (`mode`, `module_ids`).
  - Retourne le résumé et la liste des blueprints générés.

## Références code

- Endpoint MCP: `module_blueprints.py` (`/api/mcp/certifications/<int:cert_id>/generate-blueprints`).
