# Étape 9 — Orchestrateur MCP (plan d’exécution)

Cette étape expose un endpoint MCP qui vérifie l’éligibilité (`pub = 2`) et génère le plan d’exécution des étapes centrales (sync domaines, import PDF, relocate, fix, blueprints).

## Endpoints

- `POST /api/mcp/orchestrate`
  - Entrée JSON:
    - `cert_id` (obligatoire)
    - `provider_id` (optionnel)
    - `source_module_id` (optionnel)
    - `code_cert` (optionnel)
    - `file_paths`, `search_root`, `batch_size`, `workers`, `fix_action`, `blueprint_mode`, `blueprint_module_ids` (optionnels)
  - Retour :
    - `certification` (incluant `automation_eligible`, `pub_status`)
    - `plan` : liste ordonnée des endpoints et payloads recommandés.

- `POST /api/mcp/run`
  - Exécute le plan MCP (enchaînement des tools) et retourne les résultats par étape.

## Notes

- L’orchestrateur renvoie un plan à exécuter par l’agent MCP.
- L’exécution concrète des étapes reste assurée par les endpoints MCP précédents.

## Références code

- Endpoint MCP: `app.py` (`/api/mcp/orchestrate`, `/api/mcp/run`).
