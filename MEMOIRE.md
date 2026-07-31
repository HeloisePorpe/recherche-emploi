# Mémoire du projet — Recherche emploi

> Fichier de contexte pour reprendre le projet depuis n'importe quelle session
> (PC, web, mobile). Ne contient **aucun secret** : les clés API vivent
> uniquement dans les GitHub Secrets du dépôt.

## Objectif

Veille automatique d'offres d'emploi **CRM / Campaign Manager / Chef de projet CRM**
en **Île-de-France**, avec un dashboard web consultable partout et un scoring
personnalisé.

- **Site en ligne :** https://heloiseporpe.github.io/recherche-emploi/
- **Dépôt (public) :** https://github.com/HeloisePorpe/recherche-emploi
- **Hébergement :** GitHub Pages (branche `master`, dossier `/docs`).

## Comment ça marche

1. **`job_scraper.py`** interroge les sources, score et filtre les offres, puis
   écrit **`docs/jobs_output.json`**.
2. **GitHub Actions** (`.github/workflows/scraper.yml`) relance le scraper
   **chaque matin (~5h, heure de Paris)** et pousse le JSON mis à jour.
3. Le **dashboard** (`docs/index.html` + `app.js` + `styles.css`) lit ce JSON et
   l'affiche avec filtres, recherche et tri.
4. Le **suivi de candidatures** (`docs/candidatures.html` + `candidatures.js`)
   est un Kanban personnel (état stocké dans le navigateur).

## Sources d'offres

| Source | Type | Notes |
|---|---|---|
| France Travail | API | CDI, IDF, descriptions complètes |
| Adzuna | API | Descriptions **tronquées à 500 car.** |
| **Remotive** | API | 100 % remote, catégorie marketing |
| **We Work Remotely** | RSS | 100 % remote (marketing + support) |
| **Jobicy** | API | 100 % remote, filtre géo (France/Europe/anywhere) |
| **RemoteOK** | API | 100 % remote (tech-heavy) |
| **The Muse** | API | Catégorie Marketing, France + Remote |
| **Welcome to the Jungle** | Algolia | Vivier CRM/marketing FR ; best-effort (auto-découverte des clés Algolia au runtime, zone grise CGU) — **ne renvoie rien** (clés non trouvées, RSS bloqué) |
| **Alertes e-mail** | Gmail IMAP | **Indeed, HelloWork, Cadremploi, LinkedIn, WTJ** via les e-mails d'alerte reçus sur une boîte Gmail dédiée (seul moyen gratuit et légal pour ces plateformes fermées). Formats validés : Indeed (`cts.indeed.com`), HelloWork (`emails.hellowork.com/clic`), Cadremploi (`.../tr/cl`), Meteojob (`meteojob.com/jobs/<id>`). LinkedIn/WTJ à confirmer. |
| **Sites carrière (ATS)** | API | Offres publiées **uniquement sur le site des entreprises**. Interroge les API publiques **Greenhouse / Lever / SmartRecruiters / Ashby / Recruitee / Workable** pour une liste d'entreprises surveillées (`_CAREER_COMPANIES`), auto-détection de l'ATS + essai de plusieurs variantes de slug (`_slug_candidates`). Les entreprises non résolues sont listées dans les logs. `fetch_career_sites`. 11 entreprises résolues (Qonto, Doctolib, BlaBlaCar, Contentsquare, Dataiku, Mirakl, Aircall, 360Learning, Swile, Vestiaire Collective, Veepee). Thalès/Safran n'exposent pas d'ATS public. |
| Indeed | RSS | Flux bloqué (désactivé de fait) |

France Travail et Adzuna interrogés avec une liste de mots-clés élargie
(CRM manager, responsable CRM, campaign manager, email/lifecycle marketing…) ;
Adzuna récupère 2 pages par mot-clé.

Les sources remote posent `telework_days = 5` et `in_france` selon le périmètre
(France/Europe/worldwide = True ; US-only, etc. = False). Toutes les offres
passent un **filtre de pertinence** (CRM / Campaign Manager / marketing automation)
et le **salaire plancher** (42 055 € si indiqué).

## Secrets GitHub Actions (Settings → Secrets → Actions)

Valeurs **non** stockées ici. Noms attendus :

- `FRANCETRAVAIL_CLIENT_ID`, `FRANCETRAVAIL_CLIENT_SECRET`
- `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`
- `HOME_ADDRESS` *(optionnel — adresse de départ pour les trajets ; jamais dans le code public)*
- `IDFM_TOKEN` *(optionnel — trajets en transport, gratuit via prim.iledefrance-mobilites.fr)*
- `NAVITIA_TOKEN` / `GOOGLE_MAPS_API_KEY` *(optionnels — alternatives payantes)*
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` *(optionnels — boîte Gmail dédiée pour lire
  les alertes e-mail WTJ/Indeed/HelloWork/LinkedIn. Le mot de passe est un **mot de
  passe d'application** Google, 16 car., validation en 2 étapes + IMAP activés)*

### Alertes e-mail (WTJ, Indeed, HelloWork, LinkedIn)

Ces plateformes n'ont **plus d'API candidat gratuite** (Indeed a fermé sa Publisher
API en 2024 ; LinkedIn n'a aucune API Jobs tierce ; WTJ/HelloWork sont B2B). Le
scraper lit donc les **e-mails d'alerte** qu'Héloïse reçoit elle-même sur une boîte
Gmail dédiée (`heloise.emploi@gmail.com`), via IMAP (`fetch_email_alerts`) :

- Recherche les mails des 7 derniers jours (`gmail_lookback_days`) par expéditeur
  connu, dans le dossier `gmail_folder` (défaut `INBOX`).
- Extrait titre + lien de chaque offre (détection plateforme par domaine + motif
  d'URL), ignore les liens génériques (« voir toutes les offres », désabonnement).
- Best-effort, tolérant aux pannes (timeout 30 s, `try/except` → liste vide).
- Le **format exact des e-mails** de chaque plateforme peut demander un ajustement
  du parseur : transférer un exemple de mail d'alerte de chaque source pour affiner.

`config.json` est **généré en CI** à partir de ces secrets et n'est jamais commité
(il est dans `.gitignore`). Voir `SETUP_GITHUB.md` pour la mise en route.

## Champs d'une offre (`jobs_output.json`)

`source`, `title`, `link`, `company`, `location`, `description`,
`salary_raw` / `salary_extracted`, `published`, `telework_days`,
`commute_minutes`, `in_france`, `contract_type`, `score`, `score_reasons`.

## Dashboard — filtres disponibles

- Recherche texte (titre + entreprise + lieu + description)
- Tri : note / date / salaire
- Ancienneté (7 / 14 / 30 / 90 jours)
- Note minimale, salaire minimum, « avec salaire affiché uniquement »
- Télétravail uniquement, **CDI uniquement**
- **🎯 Mes critères trajet + télétravail** (**activé par défaut**) :
  - Masque les offres à **plus de 75 min** de trajet…
  - …**sauf** les postes **100 % télétravail** (en France), toujours affichés
  - Trajet non calculé → affiché et marqué « à vérifier » (masqué en mode *strict*)
  - Seuil dans `docs/app.js` : `MAX_COMMUTE = 75`
- Filtre par source
- **✅ Anti double-candidature** : une offre déjà présente dans ton suivi (ou
  détectée « postulée » par le robot email) est marquée d'un badge **« Déjà
  postulée »** sur le dashboard, **même si elle vient d'une autre plateforme**
  (rapprochement souple titre + entreprise, `candidatureForJob` dans `store.js`).
  Les offres **déjà postulées** (statut envoyé/entretien/réponse) sont **masquées
  par défaut** de l'onglet Offres (visibles dans « Mes candidatures ») ; case
  **« Afficher les offres déjà postulées »** pour les réafficher (`jobApplied`).
- **🤖 Recommandation** (robot de tri) : chaque offre reçoit `à postuler` /
  `à revoir` / `à écarter` **avec la raison**, affichée sur la carte + filtre dédié.
  Logique dans `recommend_offer` (`job_scraper.py`), en **règles** pour l'instant
  (l'IA affinera plus tard, mêmes champs `recommendation` / `recommendation_reason`).
- **Date de parution** affichée sur chaque carte
- **Archivage** des offres non pertinentes (bouton « ✕ Pas pertinent ») :
  masquées de la liste, consultables via « Voir les archivées », restaurables,
  et **exportables en JSON** (`offres-archivees.json`) pour analyse/affinage
  des filtres. Stockage : `localStorage` clé `recherche-emploi-archivees`.
- Filtres mémorisés (localStorage), compteur de filtres actifs
- Responsive : 1 col (mobile) / 2 col (tablette-portable) / auto (large)

## Filtrage à la source (`should_include` + `screen_offer`)

- **Salaire plancher** (42 055 € si indiqué) + pertinence CRM.
- **`screen_offer`** (règles issues de l'analyse des refus) à deux niveaux :
  - **Exclusion** (non ambigu, indépendant de la description) : titre
    alternance/stage/CDD/freelance (titre **ou** champ contrat Adzuna « contract »,
    ou mention explicite dans le corps) ; engineer (sauf « marketing ») ; CRM médical
    (dispositifs cardiaques) ; CRM technique/admin (titres Administrateur/Consultant
    Salesforce-Dynamics, ou signaux IT durs : SOQL, Apex, Data Loader, SSIS…) ;
    titre Customer Success / relation client ; titre commercial/vente (**+ SDR /
    sales development**) hors combo CRM ; **marketing hors cœur — disciplines
    adjacentes** (field/brand/community/événementiel **+ growth marketing / demand
    generation / product marketing / acquisition**) ; **clienteling en boutique /
    in-store** (présentiel luxe, ex. Versace) ; logistique (cariste/CACES/entrepôt) ;
    **employeur non pertinent** (Mr Pape, VeriPark, MaxAccelerate, Kennflik,
    **Qonto, Dataiku, Havas, PowerPlay, 360Learning**) ; **Alan / Doctolib exclus
    sauf titre CRM/lifecycle explicite** ; **disciplines adjacentes** (social media,
    ad-ops/programmatique, PMO stratégique, RH-ops — §2) ; freelance
    marketplace (« I will… ») ; CRM = caisse ; automobile ; présentiel explicite /
    pas de télétravail ; résidence US ou hors France obligatoire ; **télétravail /
    localisation dans un pays étranger précis** (UK, Allemagne, Espagne… ; « remote
    from UK ») — les zones larges Europe/EMEA/worldwide/anywhere restent acceptées ;
    **annonce rédigée dans une autre langue que FR/EN** (`is_foreign_language`) ;
    **télétravail connu < 2 j/sem** (0 ou 1 jour) ; **offre expirée / plus disponible**
    (« n'est plus disponible », « poste pourvu »… détecté sur le texte complet).
    L'**alternance / apprentissage / stage** est détectée dans le corps à **haute
    précision** (verbe de recrutement adjacent ou mention de contrat explicite), sans
    écarter les CDI qui disent « encadrer l'alternant » ou « hors stage/alternance ».
  - **Alerte** (`job["flags"]`, gardée + badge ⚠ au dashboard) : Customer Success /
    Account mgmt détecté dans le corps, **orientation acquisition / growth** (hors
    lifecycle), **expérience luxe/retail exigée** (prérequis dur), pertinence CRM à
    confirmer (pas de signaux marketing — fréquent sur Adzuna tronqué), contrat
    freelance/horaire/$, ESN, écart technique, séniorité/direction/management,
    résidence hors France (soft), télétravail non mentionné / faible, trajet long,
    annonce ancienne.
  - Principe : on **n'exclut** que sur des signaux non ambigus (titre, employeur,
    IT dur) ; tout ce qui dépend d'une description potentiellement tronquée reste
    en **alerte** pour ne perdre aucune offre pertinente.
- Le trajet/télétravail fin reste **délégué au dashboard** (filtre « Mes critères »).
- Dashboard : badges ⚠ sur les cartes + filtre **« Masquer les offres signalées »**.

## Scoring (`compute_score` dans `job_scraper.py`)

Base 5/10, ajusté par : outils/compétences du profil, secteurs préférés/pénalisés,
« Salesforce obligatoire » (malus), salaire vs cible, trajet, jours de télétravail,
**+1 si signaux CRM lifecycle** (email/segmentation/campagne/automation), **−2 si
orientation acquisition/growth sans lifecycle** (pondère la part réelle de CRM).

Profil candidat cible : CRM/Campaign Manager ; outils clés (emarsys, HTML/CSS,
segmentation, email/SMS…) ; salaire cible ~45–50 k€ (plancher dur 42 055 €).

## Calcul des trajets

- **Provider par défaut : IDFM PRIM** (Île-de-France Mobilités, gratuit, basé sur
  Navitia). Fallback : Navitia.io (payant) puis Google Maps (payant).
  Sélection auto selon le secret présent (`idfm` > `navitia` > `google`).
- Géocodage via la **Base Adresse Nationale** (gratuit, sans clé).
- Trajet porte-à-porte pour une arrivée à 9h un jour de semaine.
- Sans `IDFM_TOKEN` + `HOME_ADDRESS`, `commute_minutes` reste vide et le filtre
  trajet affiche « à vérifier ».
- ⚠️ L'ancienne offre gratuite **Navitia.io n'existe plus** (payante depuis 2024)
  → on utilise IDFM PRIM.

## Suivi de candidatures (Kanban)

- Page `docs/candidatures.html`, colonnes : **À postuler → Postulé → Entretien →
  Réponse**.
- Ajout depuis le dashboard (bouton « Suivre » sur chaque offre) ou manuellement.
- Glisser-déposer entre colonnes ; notes libres par candidature.
- **État privé + synchronisé** via un **dépôt GitHub privé dédié**
  **`HeloisePorpe/recherche-emploi-candidatures`** (branche `main`, fichier
  `candidatures.json` à la racine). Jamais dans le dépôt public (données privées).
  - **Lecture ET écriture** via l'API GitHub (`syncPull` / `syncPush`) avec un
    **jeton fine-grained** (accès à ce dépôt privé, *Contents: Read and write*),
    collé une fois par appareil, stocké en `localStorage`
    (`recherche-emploi-gh-token`), **jamais commité**.
  - **Avec jeton** : synchro complète multi-appareils + robot. **Sans jeton** :
    suivi local à l'appareil (rien n'est envoyé).
  - **Fusion** par `id`, dernière écriture gagnante (`updatedAt`) ; suppressions
    en *tombstone* (`deleted`) ; champ `auto` réservé au robot.
  - Le **robot** (GitHub Actions, `update_candidatures_tracking` dans
    `job_scraper.py`) lit la boîte Gmail (dossier « Tous les messages » pour voir
    aussi les mails archivés/étiquetés), classe les emails de candidature en
    priorité selon les **libellés Gmail** posés par l'utilisatrice (Confirmation
    reçue → `en_attente`, Refusée → `negatif`, En process → `positif`, Alertes →
    ignoré), sinon par règles de texte (accusés à clause conditionnelle = en
    attente, pas refus). Il écrit le champ `auto` dans le dépôt privé via l'API
    GitHub (secret **`CANDIDATURES_TOKEN`**). **Il ne CRÉE plus de carte** à
    partir des emails (trop d'erreurs : doublons, mails de bienvenue) : un email
    ne fait que **mettre à jour le statut d'une carte existante** (rapprochée par
    entreprise si le sujet est générique). Les cartes se créent uniquement depuis
    le dashboard (Suivre / ajout manuel). Le robot fusionne aussi les anciennes
    cartes email dans l'offre suivie correspondante et ne touche jamais aux
    champs utilisateur.
  - Logique côté navigateur dans `docs/store.js`. Offres **archivées** : locales.

## Dédoublonnage inter-plateformes (`_dedup`)

Une même offre diffusée sur plusieurs sources (Adzuna, France Travail, e-mails…)
est fusionnée : **titre normalisé** (ignore `H/F`, `(F/H)`, ponctuation, casse) +
entreprise souple (l'une contient l'autre, ou absente d'un côté si le titre est
assez spécifique, ≥ 20 car.). Le titre correspond aussi **par préfixe** (seuil
24 car.) : « … CRM & Clienteling » fusionne avec « … CRM & Clienteling Maison de
Luxe ». Les titres d'alerte sont d'abord **nettoyés** (`_clean_alert_title` retire
le bloc « Entreprise Ville (75) CDI 45 000 € … par an » de Meteojob et décode les
entités HTML `&#xE9;`). On garde la ligne **la plus complète** (entreprise, salaire,
description). Évite de fusionner des homonymes distincts (companies différentes).

## Contraintes connues

- Site **statique** (pas de backend) → pas de synchronisation multi-appareils du Kanban.
- Adzuna tronque ses descriptions → le télétravail n'est pas toujours détectable
  (mitigé par la récupération du texte complet des annonces).

## Pistes / idées pour la suite

- Synchroniser le Kanban entre appareils (backend léger : Firebase, Supabase, ou
  un fichier commité via l'API GitHub).
- Ajouter d'autres sources d'offres.
- Ajuster le scoring / les mots-clés du profil.

## Historique des sessions

- **Session initiale (PC, Claude Code local)** : installation Python, test du
  scraper, config API France Travail + Adzuna, construction du dashboard,
  publication GitHub Pages, mise en place du scan quotidien, dépôt public créé,
  adresse perso retirée du code, secrets replacés dans le bon dépôt.
- **Session web/cloud (celle-ci)** : filtres enrichis, filtre trajet + télétravail
  personnalisé, intégration Navitia + récupération des annonces complètes, ajout
  du suivi de candidatures, ce fichier mémoire.
