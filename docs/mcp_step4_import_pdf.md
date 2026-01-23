# Étape 4 — Service MCP: import PDF local

Cette étape expose un endpoint MCP pour importer automatiquement des questions depuis des PDF locaux dans le domaine par défaut d’une certification (ou un module explicitement fourni).

## Endpoint

- `POST /pdf/api/mcp/import-local`
  - Entrée JSON:
    - `file_paths` (liste de chemins PDF). Si absent, la recherche automatique est utilisée.
    - `search_root` (optionnel) : répertoire de recherche des PDF pour `code_cert`.
    - `module_id` (optionnel) : cible directe.
    - `cert_id` (optionnel) : utilisé pour résoudre le `code_cert`.
    - `code_cert` (optionnel) : utilisé pour résoudre le domaine default.
  - Résolution du domaine :
    - si `module_id` est absent, l’endpoint cherche un module avec `modules.code_cert = code_cert`.
    - si `code_cert` est absent mais `cert_id` fourni, il est dérivé depuis `courses.code`/`courses.name`.
  - Import :
    - extraction PDF → détection des questions → insertion en DB.
  - Retour : métriques globales + métriques par fichier.

## Recherche automatique via `code_cert`

- Si `file_paths` n’est pas fourni, l’endpoint cherche des PDF dont le nom commence par
  `{code_cert}________` (8 underscores) dans `search_root` (ou `PDF_SEARCH_ROOT` par défaut).

## Contraintes de sécurité

- Les chemins relatifs sont résolus sous `PDF_SEARCH_ROOT`.
- Les chemins absolus sont acceptés uniquement s’ils existent.
- Les fichiers doivent être des PDF.

## Références code

- Endpoint MCP: `pdf_importer.py` (`/api/mcp/import-local`).
- Résolution du domaine default: `_fetch_default_module_id()`.
