# Lab "Windows 11 Zero Trust Hardening"

Ce document détaille le scénario Windows et explique comment chaque étape du JSON `static/labs/windows_zero_trust_lab.json` a été construite.

## Contexte narratif

Le champ `scenario_md` du JSON décrit l'histoire en trois paragraphes :

1. Réception d'un nouveau lot de postes Windows 11 pour le SOC.
2. Exigences Zero Trust (pare-feu, mises à jour, journalisation) et attente du service infrastructure.
3. Aperçu du parcours : configuration locale, analyse de preuves, modélisation de l'architecture.

Ce texte est affiché dans le bandeau d'introduction du player.

## Variables dynamiques

- `device_name` : poste de travail sélectionné dans `WS-210`, `WS-455` ou `WS-889`.
- `site_code` : code site (`EU-WKS` ou `NA-HQ`).

Ces variables sont injectées dans les instructions, les validateurs et le rendu architecture (`Poste {{device_name}}`).

## Étapes du lab

### 1. `enable-firewall` (type `terminal`)

- **Objectif** : activer le pare-feu Windows sur les trois profils.
- **Commande attendue** : `powershell Set-NetFirewallProfile --Profile Domain,Private,Public --Enabled True`.
- **Impact monde** : le patch `windows.devices.<device>.firewall` enregistre l'activation et la liste des profils.
- **Points** : 20.

### 2. `configure-updates` (type `console_form`)

- **Modèle** : `windows.devices.<device>.update_policy`.
- **Champs** :
  - `ring` (toggle) → doit être positionné sur `Production`.
  - `active_hours_start` / `active_hours_end` → `08:00` / `20:00`.
  - `deadline_days` → `2`.
- **Validation** : quatre règles `kind=world` vérifient la politique enregistrée dans l'état monde.
- **Points** : 25.

### 3. `analyze-logs` (type `inspect_file` en mode `answer`)

- **Asset** : `security_snapshot.txt` (base64) comprenant les événements 7045 (service installé) et 4104 (commande PowerShell).
- **Question** : identifier le binaire déployé par `svc.deploy`.
- **Validation** : expression JavaScript qui accepte les réponses contenant `psexesvc` ou `psexec`.
- **Points** : 20.

### 4. `design-architecture` (type `architecture`)

- **Palette** : quatre composants utiles + un leurre (`legacy_gateway`). Chaque entrée définit un `icon` emoji afin que le player n'ait aucune valeur codée en dur.
- **Initial nodes** : DC01, MEMCM, Poste {{device_name}}, WSUS. Ils sont positionnés et nommés dès le chargement.
- **Commandes attendues** :
  - `DC01` → `New-ADOrganizationalUnit ... Workstations`.
  - `MEMCM` → `Invoke-CMClientAction ... Hardware Inventory`.
  - `Poste` → `New-NetIPAddress`, `Set-DnsClientServerAddress`.
  - `WSUS` → `Install-WindowsFeature ... UpdateServices`.
- **Connexion obligatoire** :
  - DC01 ↔ MEMCM,
  - MEMCM ↔ Poste,
  - MEMCM ↔ WSUS.
- **Expected world** : quatre règles `nodes` + trois règles `links`, `allow_extra_nodes=false` garantit l'unicité.
- **Validators supplémentaires** : vérifient qu'il y a exactement quatre composants et que chaque configuration n'est pas vide.
- **Points** : 30.

### 5. `hardening-quiz` (type `quiz`)

- **Question** : méthode la plus rapide pour confirmer l'application de la politique antimalware.
- **Réponse attendue** : `Get-MpComputerStatus` (réponse `a`).
- **Points** : 15.

## Scoring & transitions

- Total des points : 20 + 25 + 20 + 30 + 15 = **110** (déclaré dans `scoring.max_points`).
- Chaque étape spécifie `on_success` vers l'étape suivante et `on_failure` vers `#stay` (réessayer).

## Réutilisation

Pour créer une variation du lab :

1. Dupliquez le fichier et modifiez `variables`, `scenario_md` et les instructions.
2. Ajustez les `world_patch` et `validators` pour refléter vos contrôles.
3. Dans la section `architecture`, mettez à jour la palette et les règles `expected_world` pour couvrir votre topologie cible.

Le fichier JSON complet est disponible sous `static/labs/windows_zero_trust_lab.json`.
