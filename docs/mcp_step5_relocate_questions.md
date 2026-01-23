# Étape 5 — Service MCP: relocalisation des questions

Cette étape expose un endpoint MCP pour relocaliser automatiquement les questions d’un module source vers les domaines d’une certification cible.

## Endpoint

- `POST /reloc/api/mcp/relocate`
  - Entrée JSON:
    - `source_module_id` (obligatoire)
    - `destination_cert_id` (obligatoire)
    - `batch_size` (optionnel, défaut 10)
    - `workers` (optionnel)
  - Retour : `moved`, `total_questions`, `batches` et `errors` éventuelles.

## Références code

- Endpoint MCP: `reloc.py` (`/api/mcp/relocate`).
