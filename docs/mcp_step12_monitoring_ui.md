# Étape 12 — UI de monitoring MCP

Cette étape ajoute une page de monitoring accessible via le menu Stats > MCP.

## Route

- `GET /mcp`
  - Affiche les tools MCP disponibles et l’historique des runs.

## Contenu

- Liste des tools MCP (nom, méthode, endpoint).
- Historique des runs (`status`, `timestamp`, `results`).

## Références code

- Route UI: `app.py` (`/mcp`).
- Template: `templates/mcp.html`.
- Navigation: `templates/_nav.html` (menu Stats > MCP).
