# Connecter Slack à Data Clinic (intégration MCP Cominty)

Durée : 20 à 30 minutes. Rien à changer sur tes agents : le code active l'intégration au niveau du thread.

## 1. Préparer un espace Slack de démo

1. Si tu n'as pas d'espace Slack à toi, crée-en un gratuit sur slack.com/create (par exemple « Emna Demo »).
2. Crée un canal public nommé **#data-clinic**.
3. Garde Slack ouvert dans une fenêtre : pour la vidéo, tu filmeras l'alerte qui arrive en direct.

## 2. Créer le jeton Bot Slack (l'intégration Cominty demande un « API token »)

1. Va sur https://api.slack.com/apps → **Create New App** → **From scratch**.
   Nom : `Data Clinic`, espace de travail : ton espace de démo. **Create App**.
2. Menu de gauche **OAuth & Permissions** → section **Scopes** → **Bot Token Scopes** → **Add an OAuth Scope** :
   - `chat:write` (poster des messages)
   - `chat:write.public` (poster dans un canal public sans y être invité)
   - `channels:read` (trouver le canal par son nom)
   - `users:read` (optionnel, pour mentionner quelqu'un)
3. En haut de la même page : **Install to Workspace** → **Allow**.
4. Copie le **Bot User OAuth Token** : il commence par `xoxb-`. C'est ce jeton, et aucun autre
   (pas le « Signing Secret », pas un jeton `xoxp-` ou `xapp-`).

## 3. Connecter Slack sur la plateforme Cominty

1. platform.cominty.ai → menu **Intégrations** → carte **Slack** (dans « Vos connexions »).
2. Dans **Configuration → BearerTokenAuth → API TOKEN**, colle le jeton `xoxb-...`.
3. Clique **Tester la connexion** : le message « Slack auth test failed: invalid_auth » doit disparaître.
   Puis **Enregistrer**.
4. Dans **Agents qui peuvent l'utiliser**, active l'interrupteur de **Data Clinic Reporter** uniquement.
   Les trois autres restent désactivés (séparation des pouvoirs : seul le Reporter communique vers l'extérieur).
5. Le **nom de la connexion** est celui affiché sur la carte, en minuscules : `slack`.
   C'est la valeur de `SLACK_CONNECTION` dans ton `.env`.
6. Si la page propose une section **Configuration de l'agent** (instructions du connecteur), remplis les deux champs :

   **Quand l'utiliser (When to use)**
   ```
   Use Slack when asked to post an alert, a report summary or a notification in a Slack channel.
   ```

   **Comment l'utiliser (How to use it)**
   ```
   Default channel: #data-clinic. Post plain-text messages, one message per request.
   Start alerts with the :rotating_light: emoji and reports with the :clipboard: emoji.
   Never post files or attachments; never post in direct messages.
   ```
7. Dans Slack, ouvre #data-clinic et tape `/invite @Data Clinic` pour ajouter le bot au canal
   (facultatif si tu as ajouté le scope `chat:write.public`, mais ça évite toute surprise).

## 4. Configurer l'application

Dans `.env` :
```
SLACK_CHANNEL=#data-clinic
SLACK_CONNECTION=slack
```
Relance Streamlit. La barre latérale affiche « Intégration Slack (MCP) » avec un interrupteur.

## 5. Tester

```powershell
cd "C:\AI internship\data_clinic"
python scripts\run_pipeline.py data\sales_2026-11.csv
```
Le script affiche `== Alerte Slack` puis la réponse de l'agent : `POSTED` si le message est parti.
Si la réponse commence par `FAILED`, la raison donnée par l'agent indique quoi corriger (canal introuvable,
application non invitée, nom de connexion erroné).

## Ce qui se passe techniquement (pour l'entretien)

- Le code démarre le thread du Reporter avec
  `McpCapability(activation="always", connections=["slack"])` dans `thread_capabilities`.
  Les autres threads ont `McpCapability(activation="never")` : le Profiler, l'Auditor et le Remediator
  n'ont physiquement pas l'outil Slack installé.
- Côté Cominty, chaque connecteur MCP tourne comme un **sous-agent** avec ses propres outils. Le champ
  « Quand l'utiliser » est lu par l'agent principal pour décider de déléguer ; « Comment l'utiliser » est lu
  par le sous-agent Slack au moment d'agir.
- Deux moments déclenchent Slack : une **alerte** juste après l'Auditor si des violations de contrat sont
  détectées (max_steps=4, message dédié dans `clinic/prompts.py`), et le **résumé du rapport** à la fin.
- Un nom de connexion erroné n'est pas rejeté par l'API : l'agent répond simplement qu'il ne voit pas
  l'outil. D'où la convention `POSTED` / `FAILED: raison` imposée dans le message, qui permet à l'interface
  d'afficher un état fiable.
