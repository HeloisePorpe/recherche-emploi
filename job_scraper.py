"""
Job Scraper - CRM Campaign Manager / Chef de projet CRM
Sources : Indeed RSS, Welcome to the Jungle RSS, France Travail API
Sortie : Google Sheets + email récapitulatif

Configuration : copier config.example.json -> config.json et remplir les valeurs
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import os
import base64
import html as _html
import unicodedata
import feedparser
import requests
import json
import time
import re
import smtplib
import imaplib
import email
import email.utils
import email.header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from typing import Optional

_config_path = os.path.join(os.path.dirname(__file__), "config.json")
with open(_config_path, encoding="utf-8") as _f:
    CONFIG = json.load(_f)

CONFIG.setdefault("penalized_sectors", [
    "automobile", "automotive", "renault", "peugeot", "stellantis",
    "citroën", "toyota", "volkswagen", "bmw", "mercedes", "diac",
])
CONFIG.setdefault("preferred_sectors", [
    "banque", "bank", "assurance", "insurance", "finance",
    "axa", "bnp", "société générale", "crédit", "allianz",
    "maaf", "groupama", "natixis", "lcl", "covéa", "generali",
])
CONFIG.setdefault("salesforce_mandatory_patterns", [
    r"salesforce\s+(?:obligatoire|requis|indispensable|impératif|exigé)",
    r"(?:obligatoire|requis|indispensable|impératif|exigé)[^.]{0,30}salesforce",
    r"maîtrise\s+(?:de\s+)?salesforce\s+(?:obligatoire|requise|indispensable)",
    r"salesforce\s+(?:est\s+)?(?:un\s+)?prérequis",
])
CONFIG.setdefault("sheet_name", "Offres")
CONFIG.setdefault("google_credentials_file", "credentials.json")
CONFIG.setdefault("email_enabled", False)
CONFIG.setdefault("email_smtp_server", "smtp.gmail.com")
CONFIG.setdefault("email_smtp_port", 587)
CONFIG.setdefault("max_commute_minutes", 90)
CONFIG.setdefault("min_telework_days", 2)
CONFIG.setdefault("commute_provider", "")       # "idfm" | "navitia" | "google" | "" (auto)
CONFIG.setdefault("fetch_full_descriptions", True)  # récupère le texte complet des annonces tronquées

CANDIDATE_PROFILE = {
    "tools_expert": [
        "emarsys", "html", "css", "photoshop", "canva", "a/b test",
        "segmentation", "omnicanal", "email", "sms", "whatsapp",
    ],
    "tools_intermediate": [
        "adobe campaign", "trello", "eulerian", "google analytics", "tableau",
    ],
    "skills": [
        "marketing automation", "crm", "campagne", "campaign", "ciblage",
        "reporting", "dashboard", "workflow", "brief", "planning",
        "interservices", "coordination", "stratégique",
    ],
}


# ── RSS ────────────────────────────────────────────────────────────────────────

def build_indeed_rss_urls():
    base = "https://fr.indeed.com/rss"
    kws = ["campaign+manager+CRM", "charg%C3%A9+CRM", "chef+de+projet+CRM",
           "CRM+manager", "marketing+automation+manager"]
    return [("Indeed", f"{base}?q={k}&l=%C3%8Ele-de-France&sort=date&fromage=14&radius=50") for k in kws]


def build_wttj_rss_urls():
    kws = ["campaign-manager-crm", "crm-manager", "chef-de-projet-crm", "marketing-automation"]
    return [("Welcome to the Jungle",
             f"https://www.welcometothejungle.com/fr/jobs.rss?query={k}&aroundQuery=Paris%2C+France&aroundRadius=50")
            for k in kws]


def fetch_rss(source_name, url):
    print(f"  → {source_name} ({url[:75]}...)")
    try:
        feed = feedparser.parse(url)
        jobs = []
        for e in feed.entries:
            title = e.get("title", "")
            company = ""
            if source_name == "Indeed" and " - " in title:
                company = title.rsplit(" - ", 1)[-1].strip()
            if not company:
                company = e.get("author", e.get("dc_creator", ""))
            loc = e.get("location", "")
            if not loc:
                m = re.search(r'(?:Lieu|Location|Localisation|Ville)\s*[:\-]\s*([^\n<]+)',
                              e.get("summary", ""), re.IGNORECASE)
                loc = m.group(1).strip() if m else "Île-de-France"
            jobs.append({
                "source": source_name,
                "title": title,
                "link": e.get("link", ""),
                "company": company,
                "location": loc,
                "description": e.get("summary", "") or e.get("description", ""),
                "published": e.get("published", ""),
            })
        print(f"     {len(jobs)} offres")
        return jobs
    except Exception as ex:
        print(f"     ERREUR : {ex}")
        return []


# ── France Travail ─────────────────────────────────────────────────────────────

def fetch_francetravail_jobs():
    cid = CONFIG.get("francetravail_client_id", "")
    csec = CONFIG.get("francetravail_client_secret", "")
    if not cid or "VOTRE" in cid:
        print("  → France Travail : non configuré, ignoré")
        return []
    try:
        r = requests.post(
            "https://entreprise.francetravail.fr/connexion/oauth2/access_token",
            params={"realm": "/partenaire"},
            data={"grant_type": "client_credentials", "client_id": cid,
                  "client_secret": csec, "scope": "api_offresdemploiv2 o2dsoffre"},
            timeout=10,
        )
        r.raise_for_status()
        token = r.json().get("access_token")
    except Exception as ex:
        print(f"  → France Travail token error : {ex}")
        return []

    print("  → France Travail API...")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    all_jobs = []
    for kw in ["CRM manager", "responsable CRM", "chef de projet CRM", "chargé CRM",
               "campaign manager", "marketing automation", "email marketing",
               "lifecycle marketing", "responsable marketing CRM", "CRM télétravail"]:
        try:
            r = requests.get(
                "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search",
                headers=headers,
                params={"motsCles": kw, "typeContrat": "CDI",
                        "region": "11", "range": "0-49"},
                timeout=15,
            )
            r.raise_for_status()
            for o in r.json().get("resultats", []):
                lieu = o.get("lieuTravail", {})
                all_jobs.append({
                    "source": "France Travail",
                    "title": o.get("intitule", ""),
                    "link": o.get("origineOffre", {}).get("urlOrigine", ""),
                    "company": o.get("entreprise", {}).get("nom", ""),
                    "location": f"{lieu.get('libelle', '')} ({lieu.get('codePostal', '')})",
                    "description": o.get("description", ""),
                    "salary_raw": o.get("salaire", {}).get("libelle", ""),
                    "published": o.get("dateCreation", ""),
                    "contract_type": "CDI",  # requête typeContrat=CDI
                })
            time.sleep(0.5)
        except Exception as ex:
            print(f"     ERREUR '{kw}': {ex}")

    unique = _dedup(all_jobs)
    print(f"     {len(unique)} offres uniques")
    return unique


# ── Adzuna ─────────────────────────────────────────────────────────────────────

def fetch_adzuna_jobs():
    app_id = CONFIG.get("adzuna_app_id", "")
    app_key = CONFIG.get("adzuna_app_key", "")
    if not app_id or "VOTRE" in app_id:
        print("  → Adzuna : non configuré, ignoré")
        return []

    print("  → Adzuna API...")
    all_jobs = []
    keywords = ["CRM manager", "responsable CRM", "campaign manager", "chef de projet CRM",
                "marketing automation", "email marketing", "lifecycle marketing",
                "CRM télétravail", "campaign manager remote"]
    for kw in keywords:
        for page in (1, 2):  # 2 pages par mot-clé
            try:
                r = requests.get(
                    f"https://api.adzuna.com/v1/api/jobs/fr/search/{page}",
                    params={
                        "app_id": app_id,
                        "app_key": app_key,
                        "what": kw,
                        "where": "paris",
                        "distance": 50,
                        "results_per_page": 50,
                        "content-type": "application/json",
                    },
                    timeout=15,
                )
                r.raise_for_status()
                results = r.json().get("results", [])
                for o in results:
                    all_jobs.append({
                        "source": "Adzuna",
                        "title": o.get("title", ""),
                        "link": o.get("redirect_url", ""),
                        "company": o.get("company", {}).get("display_name", ""),
                        "location": o.get("location", {}).get("display_name", ""),
                        "description": o.get("description", ""),
                        "salary_raw": f"{int(o['salary_min'])}-{int(o['salary_max'])} €" if o.get("salary_min") else "",
                        "published": o.get("created", ""),
                        # Adzuna : "permanent" (CDI) ou "contract" (CDD / durée déterminée)
                        "contract_raw": o.get("contract_type", ""),
                    })
                time.sleep(0.4)
                if len(results) < 50:
                    break  # plus de pages
            except Exception as ex:
                print(f"     ERREUR '{kw}' p{page}: {ex}")
                break

    unique = _dedup(all_jobs)
    print(f"     {len(unique)} offres uniques")
    return unique


# ── Sources 100 % télétravail ───────────────────────────────────────────────────

_RELEVANCE_KEYWORDS = [
    "crm", "campaign manager", "campaign", "campagne", "marketing automation",
    "automation manager", "lifecycle", "email marketing", "chargé crm",
    "chef de projet crm", "customer relationship", "relation client",
]


def is_relevant(job):
    """Garde uniquement les offres CRM / Campaign Manager / marketing automation."""
    t = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    return any(k in t for k in _RELEVANCE_KEYWORDS)


# Pré-filtre structurel (étape 1) : un mot-clé CRM / marketing lifecycle DOIT
# figurer dans le titre ou la description, sinon l'offre est hors sujet et
# écartée sans évaluation (agent scolaire, assistanat médical, VP Sales…), quelle
# que soit la catégorie affichée par la source anglophone (WWR/RemoteOK…).
_CRM_KEYWORD_RE = re.compile(
    r'\bcrm\b|campagne|campaign|life ?cycle|cycle de vie|marketing automation|'
    r'\bsegmentation\b|email(?:ing)? marketing|e-?mail marketing|\bemailing\b|'
    r'marketing relationnel|fid[ée]lisation|r[ée]tention|\bretention\b|'
    r'parcours client|customer journey|r[ée]activation|marketing crm|crm marketing|'
    r'customer relationship|relation client|owned media|chargé[e]?\s+de\s+campagnes?|'
    r'braze|emarsys|adobe campaign|salesforce marketing|marketing cloud|'
    r'\bhubspot\b|klaviyo|iterable|dartagnan|selligent|\bbrevo\b', re.I)

# Rémunération anormale (millions pour un poste individuel) = signal de scam.
# Appliqué UNIQUEMENT au champ salaire (jamais à la description, pour ne pas
# confondre avec le CA / la levée de fonds de l'entreprise « €350M », « $10M »).
_COMP_SCAM_RE = re.compile(
    r'\b\d+(?:[.,]\d+)?\s*(?:m|mm|million|mio)s?\b|'
    r'\d[\s.,]?\d{3}[\s.,]?\d{3}[\s.,]?\d{3}', re.I)


# ── Filtrage fin : règles issues de l'analyse des refus ─────────────────────────
# Deux niveaux : EXCLUSION (signaux non ambigus -> offre retirée) et
# ALERTE (signaux ambigus -> offre gardée avec un badge à revoir).

# Titres toujours exclus (statut / niveau), quel que soit le reste.
_TITLE_EXCLUDE_HARD = re.compile(
    r'\b(alternance|alternant[e]?|apprenti[e]?|apprentissage|stage|stagiaire|'
    r'internship|intern|cdd|freelance|free-lance|int[ée]rim|vacataire|contractor|'
    r'fixed[- ]term|temporaire|'
    # Équivalents anglais de l'alternance / apprentissage / formation en entreprise.
    r'apprentice(?:ship)?|work[- ]stud(?:y|ies)|trainee|working student|werkstudent|'
    r'sandwich (?:course|year|placement)|placement year|industrial placement|'
    r'year in industry|co[- ]op(?:erative)?|dual (?:study|studies|education|training)|'
    r'graduate (?:scheme|programme|program|trainee)|vocational training)\b', re.I)
# Engineer / ingénieur : exclu sauf si le titre parle explicitement de marketing.
_TITLE_EXCLUDE_ENG = re.compile(r'\b(engineer|ing[ée]nieur)\b', re.I)

_MEDICAL_COMPANIES = ["abbott", "boston scientific", "medtronic", "biotronik",
                      "livanova", "microport"]
_MEDICAL_TERMS = re.compile(
    r'pacemaker|d[ée]fibrillateur|defibrillator|cardiac rhythm|cardiac|cardiaque|'
    r'electrophysiolog|[ée]lectrophysiolog|rythmologie', re.I)

_RETAIL_TERMS = re.compile(
    r'h[ôo]te?\s+de\s+caisse|encaissement|tenue de caisse|mise en rayon|'
    r'employ[ée]\s+libre[- ]service', re.I)

_HR_SOURCING = re.compile(
    r'sourcing|talent acquisition|candidate relationship|applicant tracking', re.I)

_CS_TERMS = re.compile(
    r'customer success|client success|\bcsm\b|account manager|account management|'
    r'gestion de portefeuille|portefeuille clients?|up[- ]?sell|cross[- ]?sell|'
    r'r[ée]tention|renouvellement|onboarding client|\bchurn\b', re.I)

_MARKETING_SIGNALS = re.compile(
    r'e-?mail|emailing|\bsms\b|segmentation|campagne|campaign|a/?b\s*test|'
    r'deliverab|d[ée]livrabilit|marketing automation|lifecycle|newsletter|'
    r'crm marketing|marketing crm', re.I)

_US_RESIDENCE = re.compile(
    r'must be (?:based|located|residing) in the (?:us|u\.s\.|united states)|'
    r'must reside in the (?:us|united states)|us citizenship|green card|'
    r'authori[sz]ed to work in the u\.?s|\bu\.?s\.?[- ]based\b|us[- ]based only', re.I)

# Localisation / télétravail dans un PAYS étranger précis (le poste doit être
# réalisé hors de France). Les zones larges — Europe / EMEA / worldwide /
# anywhere / remote — restent acceptées (travaillables depuis la France).
_FOREIGN_LOCATION = re.compile(
    r'\b(california|new york|texas|florida|massachusetts|washington|virginia|'
    r'san francisco|los angeles|boston|chicago|seattle|austin|denver|atlanta|'
    r'united states|\busa\b|u\.s\.a?\.|canada|toronto|vancouver|india|bangalore|'
    r'mumbai|brazil|br[ée]sil|s[ãa]o paulo|mexico|argentina|\blatam\b|\bapac\b|'
    r'australia|australie|sydney|philippines|singapore|dubai|\buae\b|abu dhabi|qatar|'
    # Pays européens précis (≠ France) : « remote from UK », etc.
    r'united kingdom|\buk\b|england|scotland|wales|ireland|\beire\b|london|manchester|'
    r'germany|deutschland|allemagne|berlin|munich|m[üu]nchen|hamburg|'
    r'spain|espagne|espa[ñn]a|madrid|barcelona|barcelone|'
    r'italy|italie|italia|rome|roma|milan|milano|'
    r'netherlands|pays-bas|amsterdam|belgium|belgique|belgi[eë]|brussels|bruxelles|'
    r'switzerland|suisse|schweiz|zurich|z[üu]rich|portugal|lisbon|lisbonne|lisboa|'
    r'poland|pologne|warsaw|varsovie|sweden|su[èe]de|stockholm|denmark|danemark|'
    r'copenhagen|copenhague|austria|autriche|vienna|vienne|norway|norv[èe]ge|'
    r'finland|finlande|greece|gr[èe]ce|romania|roumanie|luxembourg)\b',
    re.I)

_FOREIGN_RESIDENCE = re.compile(
    r'(?:based in|reside in|residents? of|located in|work from)\s+(?:the\s+)?'
    r'(united kingdom|\buk\b|canada|germany|deutschland|mexico|south africa|'
    r'spain|espagne|portugal|belgium|belgique|switzerland|suisse|india|inde)', re.I)

# Seine-Saint-Denis (93) : exclue (trajet trop long / difficile depuis Bures 91),
# sauf 100 % télétravail en France. Détection sur le LIEU uniquement : code postal
# 93xxx, « (93) », « Seine-Saint-Denis », ou une commune du 93.
_DEPT_93 = re.compile(
    r'\b93\d{3}\b|\(\s*93\s*\)|,\s*93\b|seine[- ]saint[- ]denis|'
    r'\b(saint[- ]denis|saint[- ]ouen|montreuil|bobigny|aubervilliers|'
    r'aulnay[- ]sous[- ]bois|pantin|bondy|drancy|noisy[- ]le[- ]grand|'
    r'noisy[- ]le[- ]sec|le blanc[- ]mesnil|rosny[- ]sous[- ]bois|'
    r'[ée]pinay[- ]sur[- ]seine|sevran|villepinte|la courneuve|gagny|stains|'
    r'bagnolet|les lilas|romainville|neuilly[- ]sur[- ]marne|neuilly[- ]plaisance|'
    r'livry[- ]gargan|clichy[- ]sous[- ]bois|montfermeil|tremblay[- ]en[- ]france|'
    r'villemomble|pierrefitte[- ]sur[- ]seine|le raincy|le pr[ée][- ]saint[- ]gervais|'
    r'dugny|le bourget|la plaine[- ]saint[- ]denis|coubron|vaujours|'
    r'gournay[- ]sur[- ]marne)\b', re.I)

_CONTRACT_TERMS = re.compile(
    r'independent contractor|contractor agreement|commission[- ]based|'
    r'rev(?:enue)?[- ]share|uncapped earnings|per hour|/\s*hr\b|\$\s*\d+\s*/\s*h|'
    r'south african employment|\b1099\b', re.I)

# Cabinets de conseil / ESN : télétravail dépendant du client, missions
# successives → réserve structurelle à signaler (flag, pas exclusion).
_STAFFING_COMPANIES = ["kicklox", "synopsia", "lineup7", "line up 7", "viseo",
                       "cat-amania", "cat amania", "catamania", "masao",
                       "bearingpoint", "square management", "capgemini",
                       "alpha fmc", "brain logic", "niji", "keyrus", "micropole",
                       "avanade", "sopra", "accenture", "wavestone", "colombus",
                       "colombus consulting"]
_STAFFING_TERMS = re.compile(r'\besn\b|staffing|portage salarial|r[ée]gie', re.I)

_AUTO_TERMS = re.compile(
    r'automobile|automotive|concession(?:naire)?|dealership|\bdms\b|'
    r'[ée]quipementier auto|editions techniques pour l.automobile', re.I)

_SPECIFIC_ESP = re.compile(
    r'marketo|salesforce marketing cloud|\bsfmc\b|braze|klaviyo|veeva|iterable|responsys', re.I)
_NICHE_SECTOR = re.compile(
    r'igaming|i-gaming|pharma|dispositif[s]?\s+m[ée]dica|medical device|betting|casino', re.I)
_PROG_TERMS = re.compile(r'\bpython\b|\bsql\b|javascript|\bjs\b', re.I)

_SENIOR_YEARS = re.compile(r'(\d{1,2})\s*\+?\s*(?:ans|years|an[s]?\b)', re.I)
_TEAM_MGMT = re.compile(
    r'management (?:d.une |d.)?[ée]quipe|manage a team|team management|'
    r'encadrement (?:d.une |hi[ée]rarchique|d.[ée]quipe)|team lead|head of', re.I)
_ENTRY_LEVEL = re.compile(
    r'd[ée]butant accept|junior|entry[- ]level|premier emploi|sans exp[ée]rience', re.I)
_REMOTE_MENTION = re.compile(r't[ée]l[ée]travail|remote|distanciel|home[- ]office', re.I)

# Employeurs identifiés comme non pertinents (portfolios, ESN Dynamics, tech pur…).
# Volume de faux positifs confirmé (note de calibration v3) : ces employeurs ne
# produisent que des postes hors cible (growth, produit, ops, RH, tech).
_EXCLUDE_COMPANIES = ["mr pape", "veripark", "max accelerate", "maxaccelerate",
                      "kennflik", "qonto", "dataiku", "havas", "powerplay",
                      "360learning", "360 learning"]

# Employeurs à exclure SAUF si le titre porte un vrai signal CRM/lifecycle
# (secteur autrement favorable mais beaucoup de postes hors cible). Bornes de mot
# pour éviter les collisions (« alan » dans « catalan », etc.).
_CONDITIONAL_EXCLUDE_RE = re.compile(r'\b(?:alan|doctolib)\b', re.I)

# Titres logistique / entrepôt.
_TITLE_LOGISTICS = re.compile(
    r'\b(caces|cariste|r[ée]ceptionnaire|pr[ée]parateur\s+de\s+commandes|'
    r'magasinier|manutentionnaire)\b', re.I)

# Titres Customer Success / relation client (le CRM y est un outil, pas le métier).
_TITLE_CS = re.compile(
    r'\b(customer success|client success|account manager|responsable de comptes?|'
    r'chargé[e]?\s+de\s+client[èe]le|customer care|service client)\b', re.I)

# Titres commerciaux / vente (hors cible sauf combiné CRM marketing).
_TITLE_SALES = re.compile(
    r'\b(account executive|business developer|business development|sales representative|'
    r'\bsdr\b|sales development|ing[ée]nieur\s+commercial|commercial(?:e)?\s+s[ée]dentaire|'
    r'technico[- ]commercial|chargé[e]?\s+d.affaires)\b', re.I)

# Vente en boutique (luxe) : poste 100 % présentiel, pas du CRM lifecycle —
# exclu même si « CRM » figure dans le titre (ex. Versace In-Store CRM Manager).
# NB : « clienteling » seul est gardé — il désigne souvent un poste CRM au siège
# d'une maison (« Chef de Projet CRM & Clienteling »), pas un job en boutique.
_TITLE_CLIENTELING = re.compile(r'\bin[- ]?store\b', re.I)

# Corps orienté acquisition / growth (génération de nouveaux prospects), à
# distinguer du CRM lifecycle (gestion d'une base clients existante) — cf. §2.
_ACQUISITION_BODY = re.compile(
    r'lead (?:generation|gen)\b|\bmql\b|\bsql[- ]?lead|paid (?:media|social|search)|'
    r'\babm\b|account[- ]based marketing|demand gen(?:eration)?|inbound funnel|'
    r'top of funnel|g[ée]n[ée]ration de (?:leads|prospects)|'
    r'acquisition de (?:trafic|nouveaux clients|leads|prospects)', re.I)

# Expérience luxe / retail premium exigée explicitement (prérequis dur — §10).
_LUXE_REQUIRED = re.compile(
    r'(?:exp[ée]rience|background|exp\.)[^.]{0,45}(?:dans le luxe|du luxe|en\s+luxe|'
    r'luxury|maison de luxe|retail premium)|'
    r'(?:luxe|luxury|maison de luxe)[^.]{0,30}(?:exig[ée]e?|imp[ée]rati|obligatoire|'
    r'required|indispensable|is a must|is required)', re.I)

# Titres CRM technique / admin (postes IT, jamais marketing).
_TITLE_CRM_TECH = re.compile(
    r'\b(administrateur|administrator|responsable\s+d.application|application manager)\b[^,]{0,20}\bcrm\b|'
    r'\bcrm\b[^,]{0,20}\b(administrator|administrateur|d[ée]veloppeur|developer|technique)\b|'
    r'\b(consultant|d[ée]veloppeur|developer|int[ée]grateur|architect[e]?)\b[^,]{0,25}\b(salesforce|dynamics|veeva)\b|'
    r'\b(salesforce|dynamics|veeva)\b[^,]{0,25}\b(consultant|developer|technical|functional|technico)\b', re.I)

# Signaux IT durs dans le corps : n'apparaissent jamais dans un poste marketing.
_CRM_TECH_BODY = re.compile(
    r'\bsoql\b|data loader|process builder|\bapex\b|\bssis\b|sdk crm|plugins?\s+c#|'
    r'mont[ée]es?\s+de\s+version', re.I)

# Marketing hors cœur de cible — disciplines adjacentes (§2) : terrain, marque,
# événementiel, communauté, acquisition/growth/demand gen, product marketing,
# social media, ad-ops / programmatique, PMO / programme stratégique, RH / ops.
_TITLE_OFFCORE = re.compile(
    r'\b(field marketing|growth marketing|demand generation|demand gen|'
    r'product marketing|acquisition (?:manager|marketing)|brand (?:manager|marketing)|'
    r'community manager|program manager[, ]+community|[ée]v[ée]nementiel|event manager|'
    r'social media|social campaign|ad ?operations|ad ?ops|programmatic|'
    r'media trader|media trading|trading manager|'
    r'growth (?:project|program) manager|strategic operations|partnerships? growth|'
    r'people ?(?:ops|operations)|\bhrbp\b|human resources|talent acquisition|'
    r'executive assistant|office manager|comptable|accountant|bookkeeper)\b',
    re.I)

# Profils freelance de marketplace (« I will [service] for you »).
_TITLE_FREELANCE_MP = re.compile(r'^\s*i will\b', re.I)

# Présentiel explicite / pas de télétravail.
_NO_REMOTE = re.compile(
    r'office[- ]based|no remote|100\s*%?\s*(?:sur site|pr[ée]sentiel)|'
    r'pr[ée]sentiel\s+(?:uniquement|obligatoire|complet)|sur site uniquement|'
    r'aucun t[ée]l[ée]travail|pas de t[ée]l[ée]travail', re.I)

# Résidence obligatoire hors France (exclusion, pas seulement alerte).
_FOREIGN_RESIDENCE_HARD = re.compile(
    r'must\s+(?:be\s+(?:based|located|residing)|reside)\s+in\s+(?:the\s+)?'
    r'(?!france)(united kingdom|\buk\b|canada|germany|deutschland|mexico|south africa|'
    r'spain|espagne|portugal|belgium|switzerland|india|brazil|australia)', re.I)

# Titres à séniorité / direction (alerte).
_TITLE_SENIOR = re.compile(
    r'\b(director|directeur|directrice|\bvp\b|vice[- ]president|head of|'
    r'senior manager|principal|chief)\b', re.I)

# Contrats à exclure (CDD / freelance / intérim / alternance / stage), au-delà
# du titre : détecte les mentions explicites dans le corps de l'annonce. Utile
# quand la plateforme retire le type de contrat du titre (ex. Adzuna transforme
# « … en alternance H/F » en « … H/F » mais garde « alternant(e) » dans le texte).
_CONTRACT_EXCLUDE = re.compile(
    r'contrat\s*(?:à|a)\s*dur[ée]e\s*d[ée]termin[ée]e|\bcdd\b|'
    # Intérim : signal fort et non ambigu, même isolé (« TYPE DE CONTRAT : INTERIM »).
    r'\bint[ée]rim\b|int[ée]rimaire|'
    # Durées déterminées explicites.
    r'contrat\s+de\s+\d+\s*mois|mission\s+de\s+\d+\s*mois|'
    r'(?:contrat|mission|int[ée]rim|cdd|dur[ée]e)[^.]{0,25}\bentre\s+\d+\s+et\s+\d+\s*mois|'
    # Remplacement (congé / maternité / maladie) = CDD.
    r'dans le cadre d.un remplacement|remplacement\s+(?:d.un\s+)?'
    r'(?:cong[ée]|maternit|maladie|temporaire|de\s+cong)|'
    r'\bfreelance\b|free-lance|portage salarial|fixed[- ]term', re.I)

# Alternance / apprentissage / stage dans le CORPS — détection à HAUTE PRÉCISION.
# On n'exclut que si l'annonce se décrit elle-même comme telle : un verbe de
# recrutement juste avant le mot (« recherchons un alternant »), OU une mention
# de contrat explicite (« contrat en alternance », « type de contrat : stage »).
# Objectif : ne PAS exclure un CDI qui mentionne « encadrer l'alternant » ou
# « 2 ans d'expérience hors stage/alternance ».
_ALT_STAGE_BODY = re.compile(
    r'contrat\s+(?:d.\s*apprentissage|de\s+professionnalisation|'
    r'en\s+alternance|d.\s*alternance)|'
    r'(?:recherch|recrut|propos|int[ée]gr|rejoign|deven)\w*\s+.{0,20}?'
    r'(?:alternant|apprenti(?:e|es|s)?|stagiaire)s?\b|'
    r'(?:poste|offre|mission|contrat|opportunit[ée])\s+.{0,15}?'
    r'(?:en\s+alternance|en\s+apprentissage|de\s+stage)\b|'
    r'\balternance\s+(?:de\s+)?\d+\s*(?:mois|semaines?|ans?)\b|'
    r'\bstage\s+(?:de\s+)?\d+\s*(?:mois|semaines?)\b|'
    r'type\s+de\s+contrat\s*:?\s*(?:alternance|apprentissage|stage|'
    r'contrat\s+(?:pro|de\s+professionnalisation))|'
    # Équivalents anglais : contrat d'apprentissage / work-study explicite, ou
    # recrutement d'un apprentice / trainee.
    r'\b(?:apprenticeship|work[- ]study|dual study|vocational training)\s+'
    r'(?:contract|programme?|position|scheme)|'
    r'(?:as|for|hiring|recruit\w*|seeking|looking for)\s+(?:an?\s+)?'
    r'(?:apprentice|work[- ]study|trainee)\b|'
    r'\bapprentice(?:ship)?\s+in\b', re.I)


# Détection de langue par mots-outils fréquents et distinctifs. Objectif :
# exclure les annonces ni FR ni EN (espagnol, allemand, italien, portugais…).
_LANG_WORDS = {
    "fr": {"le", "la", "les", "un", "une", "des", "du", "vous", "nous", "pour",
           "avec", "dans", "sur", "votre", "notre", "vos", "nos", "être", "au",
           "aux", "cette", "chez", "ainsi", "poste", "entreprise", "compétences",
           "expérience", "missions", "profil", "recherche", "sein", "êtes", "afin"},
    "en": {"the", "and", "you", "we", "our", "your", "for", "with", "will", "are",
           "this", "that", "role", "team", "experience", "skills", "remote", "work",
           "job", "as", "to", "of", "in", "you'll", "we're", "about", "who", "what"},
    "es": {"el", "los", "las", "una", "para", "con", "del", "que", "su", "sus",
           "nuestro", "nuestra", "será", "serás", "responsable", "empresa", "equipo",
           "experiencia", "conocimientos", "trabajo", "puesto", "además", "dentro",
           "cada", "pieza", "campañas", "diseño", "necesidades", "requisitos",
           "buscamos", "ejecutar", "asegurando", "creativo"},
    "de": {"und", "der", "die", "das", "den", "dem", "für", "mit", "wir", "ist",
           "sind", "ihre", "kenntnisse", "erfahrung", "aufgaben", "unternehmen",
           "du", "uns", "zum", "zur", "eine", "einen", "wird", "bei"},
    "it": {"azienda", "esperienza", "cerchiamo", "gestione", "nostro", "nostra",
           "competenze", "lavoro", "offriamo", "della", "che", "per", "sono",
           "siamo", "ruolo", "candidato", "sviluppo", "conoscenza"},
    "pt": {"você", "empresa", "experiência", "conhecimento", "trabalho", "nossa",
           "nosso", "para", "além", "requisitos", "sobre", "será", "equipe", "vaga",
           "dentro", "com", "responsável", "conhecimentos"},
}
_STRONG_FOREIGN_CHARS = re.compile(r'[¿¡ß]')


def is_foreign_language(text):
    """True si l'annonce est clairement rédigée dans une langue autre que FR/EN."""
    t = (text or "").lower()
    words = re.findall(r"[a-zàâäéèêëïîôöùûüçñáíóúãõ']+", t)
    if len(words) < 25:
        return False  # trop court pour juger de façon fiable
    ws = set(words)  # présence (évite qu'un mot répété fausse le compte)

    def score(lang):
        return sum(1 for w in _LANG_WORDS[lang] if w in ws)
    fren = score("fr") + score("en")
    foreign = max(score("es"), score("de"), score("it"), score("pt"))
    if _STRONG_FOREIGN_CHARS.search(t):
        foreign += 2
    # Exige un signal étranger net ET nettement supérieur au FR/EN.
    return foreign >= 5 and foreign >= fren + 3


def screen_offer(job):
    """Renvoie (exclure: bool, motif: str|None, alertes: list[str])."""
    title = job.get("title") or ""
    text = f"{title} {job.get('description') or ''} {job.get('company') or ''}"
    tl, cl = title.lower(), (job.get("company") or "").lower()
    has_mkt = bool(_MARKETING_SIGNALS.search(text))
    flags = []

    # ---- EXCLUSIONS (signaux non ambigus) ----
    if job.get("expired"):
        return True, "Offre expirée / plus disponible", flags
    # Pré-filtre structurel (étape 1) : aucun mot-clé CRM / marketing lifecycle
    # dans le titre ou la description -> hors sujet, écartée sans évaluation.
    if not _CRM_KEYWORD_RE.search(f"{title} {job.get('description') or ''}"):
        return True, "Hors sujet (aucun mot-clé CRM / marketing)", flags
    # Rémunération anormale (millions) = scam probable — testé sur le champ salaire.
    _sraw = f"{job.get('salary_raw') or ''} {job.get('salary_extracted') or ''}"
    _sval = parse_salary_value(_sraw)
    if _COMP_SCAM_RE.search(_sraw) or (_sval and _sval > 300000):
        return True, "Rémunération anormale (scam probable)", flags
    if _TITLE_EXCLUDE_HARD.search(title):
        return True, "Titre exclu (alternance / stage / CDD / freelance)", flags
    if _TITLE_EXCLUDE_ENG.search(title) and "marketing" not in tl:
        return True, "Titre exclu (engineer)", flags
    if (job.get("contract_type") == "CDD" or job.get("contract_excluded")
            or _CONTRACT_EXCLUDE.search(text)):
        return True, "Contrat exclu (CDD / freelance / intérim)", flags
    if _ALT_STAGE_BODY.search(text):
        return True, "Contrat exclu (alternance / apprentissage / stage)", flags
    if is_foreign_language(f"{title} {job.get('description') or ''}"):
        return True, "Annonce dans une autre langue (ni FR ni EN)", flags
    if _TITLE_FREELANCE_MP.search(title):
        return True, "Profil freelance marketplace (« I will… »)", flags
    if any(c in cl for c in _EXCLUDE_COMPANIES):
        return True, "Employeur non pertinent (ESN / portfolio)", flags
    if _CONDITIONAL_EXCLUDE_RE.search(cl) and not _CORE_CRM_TITLE.search(title):
        return True, "Employeur hors cible (sauf poste CRM/lifecycle explicite)", flags
    if any(c in cl for c in _MEDICAL_COMPANIES) or _MEDICAL_TERMS.search(text):
        return True, "CRM médical (dispositifs cardiaques)", flags
    if _TITLE_LOGISTICS.search(title):
        return True, "Titre logistique (cariste / CACES / entrepôt)", flags
    if _TITLE_CRM_TECH.search(title) or _CRM_TECH_BODY.search(text):
        return True, "CRM technique / admin (IT, pas marketing)", flags
    if _TITLE_CS.search(title) and "marketing" not in tl and "campaign" not in tl:
        return True, "Titre Customer Success / relation client", flags
    if _TITLE_SALES.search(title) and "crm" not in tl:
        return True, "Titre commercial / vente", flags
    if _TITLE_OFFCORE.search(title) and "crm" not in tl:
        return True, "Marketing hors cœur (acquisition / growth / marque / terrain)", flags
    if _TITLE_CLIENTELING.search(title):
        return True, "CRM clienteling / boutique (présentiel)", flags
    if _RETAIL_TERMS.search(text):
        return True, "CRM = caisse / magasin", flags
    if _AUTO_TERMS.search(text):
        return True, "Secteur automobile", flags
    if _NO_REMOTE.search(text):
        return True, "Présentiel / pas de télétravail", flags
    tw = job.get("telework_days")
    if isinstance(tw, int) and tw < 2:
        return True, f"Télétravail insuffisant ({tw} j/sem, min. 2)", flags
    if _US_RESIDENCE.search(text):
        return True, "Résidence / citoyenneté US requise", flags
    if _FOREIGN_RESIDENCE_HARD.search(text):
        return True, "Résidence hors France obligatoire", flags
    loc = job.get("location") or ""
    if _FOREIGN_LOCATION.search(loc) and "france" not in loc.lower():
        return True, "Télétravail / localisation hors France", flags
    # Seine-Saint-Denis (93) exclu, sauf 100 % télétravail en France.
    if _DEPT_93.search(loc) and not (job.get("telework_days") == 5 and job.get("in_france", True)):
        return True, "Localisation en Seine-Saint-Denis (93)", flags

    # ---- ALERTES (signaux ambigus, on garde et on signale) ----
    if _ACQUISITION_BODY.search(text) and not has_mkt:
        flags.append("Orientation acquisition / growth ?")
    if _LUXE_REQUIRED.search(text):
        flags.append("Expérience luxe / retail exigée ?")
    if _CS_TERMS.search(text) and not has_mkt:
        flags.append("Customer Success / Account mgmt ?")
    if _HR_SOURCING.search(text) and not has_mkt:
        flags.append("CRM = sourcing RH ?")
    if "crm" in text.lower() and not has_mkt:
        flags.append("Pertinence CRM à confirmer")
    if re.search(r'manager|director', tl) and re.search(r'\bteam\b|coach', tl) \
            and re.search(r'\bcsm\b|client success', text.lower()):
        flags.append("Poste managérial d'équipe")
    if _FOREIGN_RESIDENCE.search(text):
        flags.append("Résidence hors France ?")
    if _CONTRACT_TERMS.search(text):
        flags.append("Contrat à vérifier (freelance / horaire / $)")
    sal_raw = (job.get("salary_raw") or "")
    if ("$" in sal_raw or "usd" in sal_raw.lower()) and "€" not in sal_raw:
        flags.append("Salaire en USD")
    if any(s in cl for s in _STAFFING_COMPANIES) or _STAFFING_TERMS.search(text):
        # Réserve levée si le télétravail est garanti sans condition de mission.
        if not re.search(r't[ée]l[ée]travail\s+(?:flexible|illimit|sans\s+(?:limit|condition))|'
                         r'full\s*remote\s+(?:garanti|permanent)', text, re.I):
            flags.append("Conseil / ESN — télétravail dépendant de la mission ?")
    if _SPECIFIC_ESP.search(text) and _PROG_TERMS.search(text) and _NICHE_SECTOR.search(text):
        flags.append("Écart technique large")
    if any(int(y) > 7 for y in _SENIOR_YEARS.findall(text)):
        flags.append("Séniorité élevée (>7 ans ?)")
    if _TEAM_MGMT.search(text) or _TITLE_SENIOR.search(title):
        flags.append("Séniorité / management d'équipe ?")
    if _ENTRY_LEVEL.search(text):
        flags.append("Poste junior / débutant ?")

    # tw < 2 est déjà exclu plus haut ; ici on ne signale que l'info manquante.
    if job.get("telework_days") is None and not _REMOTE_MENTION.search(text):
        flags.append("Télétravail non mentionné")

    cm = job.get("commute_minutes")
    if isinstance(cm, (int, float)) and cm > 90:
        flags.append(f"Trajet long ({int(cm)} min)")

    pub = job.get("published")
    if pub:
        try:
            d = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            age = (datetime.now(d.tzinfo) - d).days
            if age > 75:
                flags.append(f"Annonce ancienne ({age} j)")
        except Exception:
            pass

    return False, None, flags


_REMOTE_OUT_OF_REACH = [
    "usa", "united states", "u.s.", "canada", "brazil", "brésil", "india", "inde",
    "australia", "australie", "latam", "apac", "argentina", "mexico", "philippines",
]


def remote_scope_in_france(text):
    """Une offre 100 % remote est-elle travaillable depuis la France ?
    True si périmètre France/Europe/worldwide (ou inconnu), False si clairement
    limité à une zone lointaine (US-only, etc.)."""
    t = (text or "").lower()
    if not t:
        return True
    if any(k in t for k in ["france", "europe", "emea", "worldwide", "anywhere",
                            "global", "european", "remote"]):
        return True
    if any(k in t for k in _REMOTE_OUT_OF_REACH):
        return False
    return True


def _range_salary(lo, hi, cur="€"):
    if not lo:
        return ""
    try:
        return f"{int(lo)}-{int(hi or lo)} {cur}"
    except Exception:
        return ""


def fetch_remotive_jobs():
    print("  → Remotive API...")
    jobs = []
    try:
        r = requests.get("https://remotive.com/api/remote-jobs",
                         params={"category": "marketing", "limit": 100},
                         headers={"User-Agent": "JobScraper/1.0"}, timeout=15)
        r.raise_for_status()
        for o in r.json().get("jobs", []):
            loc = o.get("candidate_required_location", "") or ""
            jobs.append({
                "source": "Remotive",
                "title": o.get("title", ""),
                "link": o.get("url", ""),
                "company": o.get("company_name", ""),
                "location": loc or "Remote",
                "description": o.get("description", ""),
                "salary_raw": o.get("salary", "") or "",
                "published": o.get("publication_date", ""),
                "telework_days": 5,
                "in_france": remote_scope_in_france(loc),
            })
    except Exception as ex:
        print(f"     ERREUR Remotive : {ex}")
    jobs = [j for j in jobs if is_relevant(j)]
    print(f"     {len(jobs)} offres pertinentes")
    return jobs


def fetch_weworkremotely_jobs():
    print("  → We Work Remotely (RSS)...")
    feeds = [
        "https://weworkremotely.com/categories/remote-marketing-jobs.rss",
        "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    ]
    jobs = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                title = e.get("title", "")
                company = ""
                if ":" in title:
                    company, title = title.split(":", 1)
                    company, title = company.strip(), title.strip()
                region = e.get("region", "") or e.get("summary", "") or ""
                jobs.append({
                    "source": "We Work Remotely",
                    "title": title,
                    "link": e.get("link", ""),
                    "company": company,
                    "location": (e.get("region", "") or "Remote"),
                    "description": e.get("summary", "") or e.get("description", ""),
                    "published": e.get("published", ""),
                    "telework_days": 5,
                    "in_france": remote_scope_in_france(region),
                })
        except Exception as ex:
            print(f"     ERREUR WWR : {ex}")
    jobs = [j for j in jobs if is_relevant(j)]
    unique = _dedup(jobs)
    print(f"     {len(unique)} offres pertinentes")
    return unique


def fetch_jobicy_jobs():
    print("  → Jobicy API...")
    jobs = []
    for params in [{"count": 50, "geo": "france", "tag": "crm"},
                   {"count": 50, "geo": "europe", "tag": "marketing"},
                   {"count": 50, "geo": "anywhere", "tag": "crm"}]:
        try:
            r = requests.get("https://jobicy.com/api/v2/remote-jobs", params=params,
                             headers={"User-Agent": "JobScraper/1.0"}, timeout=15)
            r.raise_for_status()
            for o in r.json().get("jobs", []):
                geo = o.get("jobGeo", "") or ""
                jobs.append({
                    "source": "Jobicy",
                    "title": o.get("jobTitle", ""),
                    "link": o.get("url", ""),
                    "company": o.get("companyName", ""),
                    "location": geo or "Remote",
                    "description": o.get("jobExcerpt", "") or o.get("jobDescription", ""),
                    "salary_raw": _range_salary(o.get("annualSalaryMin"),
                                                o.get("annualSalaryMax"),
                                                o.get("salaryCurrency", "€")),
                    "published": o.get("pubDate", ""),
                    "telework_days": 5,
                    "in_france": remote_scope_in_france(geo),
                })
            time.sleep(0.3)
        except Exception as ex:
            print(f"     ERREUR Jobicy : {ex}")
    jobs = [j for j in jobs if is_relevant(j)]
    unique = _dedup(jobs)
    print(f"     {len(unique)} offres pertinentes")
    return unique


def fetch_remoteok_jobs():
    print("  → RemoteOK API...")
    jobs = []
    try:
        r = requests.get("https://remoteok.com/api",
                         headers={"User-Agent": "Mozilla/5.0 (compatible; JobScraper/1.0)"},
                         timeout=15)
        r.raise_for_status()
        for o in r.json():
            if not isinstance(o, dict) or not o.get("position"):
                continue  # 1er élément = mention légale
            loc = o.get("location", "") or ""
            jobs.append({
                "source": "RemoteOK",
                "title": o.get("position", ""),
                "link": o.get("url", ""),
                "company": o.get("company", ""),
                "location": loc or "Remote",
                "description": o.get("description", ""),
                "salary_raw": _range_salary(o.get("salary_min"), o.get("salary_max"), "$"),
                "published": o.get("date", ""),
                "telework_days": 5,
                "in_france": remote_scope_in_france(loc),
            })
    except Exception as ex:
        print(f"     ERREUR RemoteOK : {ex}")
    jobs = [j for j in jobs if is_relevant(j)]
    print(f"     {len(jobs)} offres pertinentes")
    return jobs


def fetch_themuse_jobs():
    """The Muse : API publique gratuite, catégorie Marketing, France + Remote."""
    print("  → The Muse API...")
    jobs = []
    for loc in ["France", "Flexible / Remote"]:
        for page in range(0, 3):
            try:
                r = requests.get(
                    "https://www.themuse.com/api/public/jobs",
                    params={"category": "Marketing", "location": loc, "page": page},
                    headers={"User-Agent": "JobScraper/1.0"}, timeout=15)
                if r.status_code != 200:
                    break
                results = r.json().get("results", [])
                for o in results:
                    locs = ", ".join(l.get("name", "") for l in o.get("locations", [])) or loc
                    is_remote = bool(re.search(r'flexible|remote|t[ée]l[ée]travail', locs, re.I))
                    jobs.append({
                        "source": "The Muse",
                        "title": o.get("name", ""),
                        "link": (o.get("refs", {}) or {}).get("landing_page", ""),
                        "company": (o.get("company", {}) or {}).get("name", ""),
                        "location": locs,
                        "description": o.get("contents", ""),
                        "published": o.get("publication_date", ""),
                        "telework_days": 5 if is_remote else None,
                        "in_france": remote_scope_in_france(locs),
                    })
                if len(results) < 20:
                    break
                time.sleep(0.3)
            except Exception as ex:
                print(f"     ERREUR The Muse ({loc} p{page}) : {ex}")
                break
    jobs = [j for j in jobs if is_relevant(j)]
    unique = _dedup(jobs)
    print(f"     {len(unique)} offres pertinentes")
    return unique


def _wttj_algolia_config():
    """Découvre au runtime l'App ID, la clé de recherche et l'index Algolia
    de Welcome to the Jungle (valeurs publiques embarquées dans leur front)."""
    try:
        r = requests.get("https://www.welcometothejungle.com/fr/jobs",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        html = r.text
        app = re.search(r'algolia[^"\']{0,25}app(?:lication)?[_-]?id["\']?\s*[:=]\s*["\']([A-Z0-9]{8,12})', html, re.I)
        key = re.search(r'algolia[^"\']{0,30}(?:api[_-]?)?key[^"\']{0,12}["\']?\s*[:=]\s*["\']([a-f0-9]{24,})', html, re.I)
        idx = re.search(r'["\'](wk_[a-z0-9_]+|[a-z_]*jobs[a-z_]*prod[a-z_]*)["\']', html, re.I)
        if app and key and idx:
            return app.group(1), key.group(1), idx.group(1)
        print("     WTTJ : identifiants Algolia non trouvés dans la page")
    except Exception as ex:
        print(f"     WTTJ config error : {ex}")
    return None


def fetch_wttj_jobs():
    """Welcome to the Jungle via son index Algolia public (best-effort, zone grise CGU)."""
    print("  → Welcome to the Jungle (Algolia)...")
    cfg = _wttj_algolia_config()
    if not cfg:
        return []
    app, key, index = cfg
    jobs = []
    for q in ["CRM", "campaign manager", "marketing automation", "email marketing"]:
        try:
            r = requests.post(
                f"https://{app}-dsn.algolia.net/1/indexes/{index}/query",
                headers={"X-Algolia-Application-Id": app, "X-Algolia-API-Key": key,
                         "Content-Type": "application/json"},
                json={"params": f"query={q}&hitsPerPage=40"},
                timeout=15)
            r.raise_for_status()
            for h in r.json().get("hits", []):
                org = h.get("organization", {}) or {}
                offices = h.get("offices", []) or []
                o0 = offices[0] if offices else {}
                loc = ", ".join(x for x in [o0.get("city", ""), o0.get("country", "")] if x) or "France"
                slug, oslug = h.get("slug", ""), org.get("slug", "")
                link = (f"https://www.welcometothejungle.com/fr/companies/{oslug}/jobs/{slug}"
                        if oslug and slug else "")
                jobs.append({
                    "source": "Welcome to the Jungle",
                    "title": h.get("name", ""),
                    "link": link,
                    "company": org.get("name", ""),
                    "location": loc,
                    "description": h.get("description", "") or "",
                    "published": h.get("published_at", ""),
                })
            time.sleep(0.3)
        except Exception as ex:
            print(f"     ERREUR WTTJ '{q}' : {ex}")
    jobs = [j for j in jobs if is_relevant(j)]
    unique = _dedup(jobs)
    print(f"     {len(unique)} offres pertinentes")
    return unique


# ── Alertes e-mail (Gmail IMAP) ────────────────────────────────────────────────
#
# Récupère les offres depuis les e-mails d'alerte des grandes plateformes
# (Welcome to the Jungle, Indeed, HelloWork, LinkedIn…) reçus sur une boîte
# Gmail dédiée. Ces plateformes n'ayant plus d'API candidat gratuite, on lit
# les alertes que l'utilisatrice reçoit elle-même (usage personnel, conforme).
#
# Prérequis (secrets GitHub, jamais dans le code) :
#   GMAIL_ADDRESS       adresse de la boîte dédiée
#   GMAIL_APP_PASSWORD  mot de passe d'application Google (16 car., 2FA requise)
# Optionnel : gmail_folder (défaut INBOX), gmail_lookback_days (défaut 7).

# Détection de la plateforme par expéditeur + motif d'URL des offres.
_EMAIL_ALERT_SOURCES = [
    {"name": "Welcome to the Jungle (alerte)",
     "senders": ["welcometothejungle.com", "wttj.co"],
     "link_re": re.compile(r'https?://[^"\'\s>]*welcometothejungle\.com/[^"\'\s>]*/jobs/[^"\'\s>]+', re.I)},
    {"name": "Indeed (alerte)",
     "senders": ["indeed.com", "indeedemail.com", "match.indeed.com"],
     # Les alertes Indeed enrobent les liens d'offres dans des URL de tracking
     # cts.indeed.com/v3/... (en plus des formats rc/clk, viewjob, pagead).
     "link_re": re.compile(r'https?://(?:[^"\'\s>]*\.)?indeed\.com/(?:v3/|rc/clk|viewjob|pagead|job|m/)[^"\'\s>]+', re.I)},
    {"name": "HelloWork (alerte)",
     "senders": ["hellowork.com", "hellowork-group.com", "regionsjob.com"],
     # Les alertes HelloWork enrobent les liens dans emails.hellowork.com/clic/...
     "link_re": re.compile(
         r'https?://(?:emails\.hellowork\.com/clic/|[^"\'\s>]*hellowork\.com/[^"\'\s>]*(?:emploi|offre))[^"\'\s>]+', re.I)},
    {"name": "LinkedIn (alerte)",
     "senders": ["linkedin.com", "e.linkedin.com", "jobs-listings@linkedin.com"],
     "link_re": re.compile(r'https?://[^"\'\s>]*linkedin\.com/(?:comm/)?jobs/view/[^"\'\s>]+', re.I)},
    {"name": "Cadremploi (alerte)",
     "senders": ["cadremploi.fr", "alertes.cadremploi.fr"],
     # Alertes Cadremploi : liens enrobés dans r.emails*.alertes.cadremploi.fr/tr/cl/...
     "link_re": re.compile(
         r'https?://[^"\'\s>]*cadremploi\.fr/(?:tr/cl/|emploi|offre|annonce)[^"\'\s>]+', re.I)},
    {"name": "Meteojob (alerte)",
     "senders": ["meteojob.com", "cleverconnect"],
     # Meteojob : liens d'offres directs www.meteojob.com/jobs/<id>
     "link_re": re.compile(r'https?://[^"\'\s>]*meteojob\.com/jobs/\d+', re.I)},
]

_HTML_TAG_RE = re.compile(r'<[^>]+>')
_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<text>.*?)</a>',
                        re.I | re.S)


def _email_body_html(msg):
    """Extrait le corps d'un e-mail (préfère le HTML, sinon le texte)."""
    html, text = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            ctype = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                content = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/html":
                html += content
            elif ctype == "text/plain":
                text += content
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            content = ""
        if msg.get_content_type() == "text/html":
            html = content
        else:
            text = content
    return html, text


# Marqueurs de genre servant de fin de titre « propre » (avant le bloc parasite).
_GENDER_MARKER_RE = re.compile(
    r'\((?:H\s*/?\s*F|F\s*/?\s*H|F\s*/?\s*M\s*/?\s*X|M\s*/?\s*F\s*/?\s*X|'
    r'H\s*/?\s*F\s*/?\s*X|W\s*/?\s*M)\)|\b(?:H/?F|F/?H)\b', re.I)
# Bloc « parasite » ajouté par certaines alertes (Meteojob, Page Personnel…) :
# « … Ville (75) CDI 45 000 € - 53 000 € par an ». Sert à détecter qu'il faut
# nettoyer le titre (et non à couper : la coupe se fait au marqueur de genre).
_ALERT_BOILER_RE = re.compile(
    r'\(\d{2,3}\)\s+(?:CDI|CDD|Alternance|Stage|Freelance|Int[ée]rim|'
    r'Ind[ée]pendant|Apprentissage)\b|\d[\d\s]{2,}€.*\bpar\s+(?:an|mois|jour)\b', re.I)
_ALERT_CITY_TAIL_RE = re.compile(
    r'\s+[A-ZÀ-Ÿ][\wÀ-ÿ\'’.\- ]*?\(\d{2,3}\)\s+(?:CDI|CDD|Alternance|Stage|'
    r'Freelance|Int[ée]rim|Ind[ée]pendant|Apprentissage)\b.*$', re.I)


def _clean_alert_title(title):
    """Retire le bloc « Entreprise Ville (75) CDI 45 000 € … par an » que certaines
    alertes (Meteojob…) collent après le vrai titre. Sans ce nettoyage, le titre
    ne correspond plus à la même offre vue sur Adzuna / France Travail, et le
    dédoublonnage échoue. On ne touche qu'aux titres qui portent ce bloc."""
    t = re.sub(r"\s+", " ", title or "").strip()
    if not _ALERT_BOILER_RE.search(t):
        return t
    m = _GENDER_MARKER_RE.search(t)
    if m:
        return t[:m.end()].strip()
    t = _ALERT_CITY_TAIL_RE.sub("", t).strip()
    t = re.sub(r"\s+\d[\d\s]{2,}€.*$", "", t).strip()
    return t


def _parse_alert_email(msg, cfg):
    """Extrait les offres d'un e-mail d'alerte pour une plateforme donnée."""
    html, _text = _email_body_html(msg)
    if not html:
        return []
    try:
        published = email.utils.parsedate_to_datetime(msg.get("Date", "")).isoformat()
    except Exception:
        published = ""
    link_re = cfg["link_re"]
    jobs, seen = [], set()
    for m in _ANCHOR_RE.finditer(html):
        href = m.group("href")
        if not link_re.match(href) and not link_re.search(href):
            continue
        title = _HTML_TAG_RE.sub("", m.group("text"))
        title = _html.unescape(title)  # &#xE9; -> é, &#xB7; -> ·, &amp; -> &
        title = re.sub(r"\s+", " ", title).strip()
        title = _clean_alert_title(title)
        if not title or len(title) < 3:
            continue
        # Ignore les liens génériques (voir toutes les offres, se désabonner…).
        if re.search(r"voir (toutes|tous|l'offre|plus|cette|d'autres)|see all|unsubscribe|"
                     r"d[ée]sabonn|g[ée]rer|param[èe]tr|postul|mettre à jour|pr[ée]f[ée]rence|"
                     r"t[ée]l[ée]charger|centre d'aide|conditions|confidentialit|"
                     r"cr[ée]er (mon|une) alerte|d[ée]poser mon cv|diffuser|s'inscrire|"
                     r"se connecter|acc[èe]s recruteur|lire dans l'app|me pr[ée]parer|"
                     r"^indeed$|^linkedin$|^cadremploi$|app\s?store|google\s?play|^\W*$", title, re.I):
            continue
        key = href.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        jobs.append({
            "source": cfg["name"],
            "title": title,
            "link": href,
            "company": "",
            "location": "",
            "description": "",
            "published": published,
            "in_france": True,
        })
    return jobs


def fetch_email_alerts():
    """Offres issues des e-mails d'alerte (Gmail IMAP, boîte dédiée)."""
    print("  → Alertes e-mail (Gmail IMAP)...")
    address = CONFIG.get("gmail_address", "")
    password = CONFIG.get("gmail_app_password", "")
    if not address or not password:
        print("     Gmail non configuré (GMAIL_ADDRESS / GMAIL_APP_PASSWORD) — ignoré")
        return []
    folder = CONFIG.get("gmail_folder", "INBOX")
    lookback = int(CONFIG.get("gmail_lookback_days", 7))
    since = (datetime.now() - timedelta(days=lookback)).strftime("%d-%b-%Y")
    jobs = []
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        imap.login(address, password)
        imap.select(f'"{folder}"', readonly=True)
        for cfg in _EMAIL_ALERT_SOURCES:
            uids = set()
            for sender in cfg["senders"]:
                try:
                    typ, data = imap.search(None, "SINCE", since, "FROM", sender)
                    if typ == "OK" and data and data[0]:
                        uids.update(data[0].split())
                except Exception:
                    continue
            for uid in uids:
                try:
                    typ, msg_data = imap.fetch(uid, "(RFC822)")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    jobs.extend(_parse_alert_email(msg, cfg))
                except Exception as ex:
                    print(f"     lecture mail {cfg['name']} : {ex}")
        try:
            imap.close()
        except Exception:
            pass
        imap.logout()
    except Exception as ex:
        print(f"     ERREUR Gmail IMAP : {ex}")
        return []
    unique = _dedup(jobs)
    by_src = {}
    for j in unique:
        by_src[j["source"]] = by_src.get(j["source"], 0) + 1
    for src, n in sorted(by_src.items()):
        print(f"     {src} : {n}")
    print(f"     {len(unique)} offres (alertes e-mail)")
    return unique


# ── Sites carrière (ATS) ────────────────────────────────────────────────────────
#
# Beaucoup d'entreprises ne publient que sur leur propre site, via un ATS.
# On interroge les API publiques des grands ATS (Greenhouse, Lever,
# SmartRecruiters). Pour chaque entreprise on essaie chaque ATS avec son slug
# jusqu'à trouver des offres. Best-effort ; ne teste que depuis un réseau ouvert
# (GitHub Actions). Les offres passent ensuite les mêmes filtres (pertinence CRM,
# trajet / télétravail, langue, etc.).

# Entreprises à surveiller. `slug` = identifiant dans l'URL de l'ATS (souvent le
# nom en minuscules sans espaces). `ats` optionnel force un ATS ; sinon on essaie
# tout. Liste enrichie au fil des retours de scan.
_CAREER_COMPANIES = [
    # `ats` fige un ATS (évite d'essayer les autres) ; `slug` = identifiant confirmé ;
    # `slugs` = variantes supplémentaires à tester. Sinon on essaie tous les ATS avec
    # des variantes de nom générées automatiquement.
    # Qonto, Dataiku, 360Learning retirés : 0 offre pertinente (note v3),
    # désormais exclus par employeur (inutile de les interroger).
    {"name": "Doctolib", "slug": "doctolib", "ats": "greenhouse"},
    {"name": "BlaBlaCar", "slug": "blablacar", "ats": "lever"},
    {"name": "Contentsquare", "slug": "contentsquare", "ats": "lever"},
    {"name": "Mirakl", "slug": "mirakl", "ats": "greenhouse"},
    {"name": "Aircall", "slug": "aircall", "ats": "lever"},
    {"name": "Swile", "slug": "swile", "ats": "lever"},
    {"name": "Vestiaire Collective", "slug": "vestiairecollective", "ats": "lever"},
    {"name": "Veepee", "slug": "veepee", "ats": "lever"},
    # Non encore résolues : on teste tous les ATS + variantes de slug.
    {"name": "Back Market", "slug": "backmarket", "slugs": ["back-market", "backmarketjobs"]},
    {"name": "Alan", "slug": "alan", "slugs": ["alanhq", "join-alan"]},
    {"name": "Spendesk", "slug": "spendesk"},
    {"name": "PayFit", "slug": "payfit", "slugs": ["payfitjobs"]},
    {"name": "Deezer", "slug": "deezer", "slugs": ["Deezer"]},
    {"name": "Ledger", "slug": "ledger", "slugs": ["ledgerhq"]},
    {"name": "ManoMano", "slug": "manomano", "slugs": ["colibri"]},
    {"name": "Sephora", "slug": "sephora", "slugs": ["Sephora", "SephoraUSA"]},
    {"name": "Believe", "slug": "believe", "slugs": ["believedigital", "Believe"]},
    {"name": "Thales", "slug": "thales", "slugs": ["thalesgroup", "Thales", "ThalesGroup"]},
    {"name": "Safran", "slug": "safran", "slugs": ["safrangroup", "safran-group", "Safran"]},
]

_CAREER_LOC_OK = re.compile(
    r'france|paris|[îi]le[- ]de[- ]france|\bidf\b|remote|t[ée]l[ée]travail|'
    r'hybrid|anywhere|europe', re.I)


def _career_location_ok(loc):
    """Garde France / Paris / remote / Europe ; écarte les localisations
    clairement étrangères. Inconnu -> gardé (le filtre trajet tranchera)."""
    if not loc:
        return True
    if _FOREIGN_LOCATION.search(loc) and "france" not in loc.lower():
        return False
    return bool(_CAREER_LOC_OK.search(loc))


def _career_job(name, title, link, location, description="", published=""):
    return {"source": f"Site carrière — {name}", "title": title or "", "link": link or "",
            "company": name, "location": location or "", "description": description or "",
            "published": published or ""}


_ATS_UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _fetch_greenhouse(slug, name):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, timeout=10, headers=_ATS_UA)
    if r.status_code != 200:
        return None
    jobs = []
    for o in r.json().get("jobs", []):
        loc = (o.get("location") or {}).get("name", "")
        desc = re.sub(r"<[^>]+>", " ", o.get("content", "") or "")
        jobs.append(_career_job(name, o.get("title", ""), o.get("absolute_url", ""),
                                 loc, desc, o.get("updated_at", "")))
    return jobs


def _fetch_lever(slug, name):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, timeout=10, headers=_ATS_UA)
    if r.status_code != 200:
        return None
    data = r.json()
    if not isinstance(data, list):
        return None
    jobs = []
    for o in data:
        cat = o.get("categories") or {}
        loc = cat.get("location", "") or ""
        pub = ""
        try:
            pub = datetime.fromtimestamp(o.get("createdAt", 0) / 1000).isoformat()
        except Exception:
            pass
        jobs.append(_career_job(name, o.get("text", ""), o.get("hostedUrl", ""),
                                 loc, o.get("descriptionPlain", ""), pub))
    return jobs


def _fetch_smartrecruiters(slug, name):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    r = requests.get(url, timeout=10, headers=_ATS_UA)
    if r.status_code != 200:
        return None
    jobs = []
    for o in r.json().get("content", []):
        loc = o.get("location") or {}
        locs = ", ".join(x for x in [loc.get("city", ""), loc.get("country", "")] if x)
        link = (f"https://jobs.smartrecruiters.com/{slug}/{o.get('id','')}"
                if o.get("id") else "")
        jobs.append(_career_job(name, o.get("name", ""), link, locs, "",
                                 o.get("releasedDate", "")))
    return jobs


def _fetch_ashby(slug, name):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
    r = requests.get(url, timeout=10, headers=_ATS_UA)
    if r.status_code != 200:
        return None
    jobs = []
    for o in r.json().get("jobs", []):
        loc = o.get("location", "") or ""
        desc = o.get("descriptionPlain") or re.sub(r"<[^>]+>", " ", o.get("descriptionHtml", "") or "")
        jobs.append(_career_job(name, o.get("title", ""),
                                 o.get("jobUrl", "") or o.get("applyUrl", ""),
                                 loc, desc, o.get("publishedAt", "")))
    return jobs


def _fetch_recruitee(slug, name):
    url = f"https://{slug}.recruitee.com/api/offers/"
    r = requests.get(url, timeout=10, headers=_ATS_UA)
    if r.status_code != 200:
        return None
    jobs = []
    for o in r.json().get("offers", []):
        loc = o.get("location") or ", ".join(
            x for x in [o.get("city", ""), o.get("country", "")] if x)
        jobs.append(_career_job(name, o.get("title", ""),
                                 o.get("careers_url") or o.get("careers_apply_url", ""),
                                 loc, re.sub(r"<[^>]+>", " ", o.get("description", "") or ""),
                                 o.get("published_at", "")))
    return jobs


def _fetch_workable(slug, name):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    r = requests.get(url, timeout=10, headers=_ATS_UA)
    if r.status_code != 200:
        return None
    jobs = []
    for o in r.json().get("jobs", []):
        loc = ", ".join(x for x in [o.get("city", ""), o.get("country", "")] if x) \
            or o.get("location", "")
        link = o.get("url") or o.get("shortlink") or o.get("application_url", "")
        jobs.append(_career_job(name, o.get("title", ""), link, loc,
                                 o.get("description", ""), o.get("published_on", "")))
    return jobs


# Ordre d'essai : les ATS à API publique fiable d'abord.
_ATS_FETCHERS = {"greenhouse": _fetch_greenhouse, "lever": _fetch_lever,
                 "smartrecruiters": _fetch_smartrecruiters, "ashby": _fetch_ashby,
                 "recruitee": _fetch_recruitee, "workable": _fetch_workable}
_ATS_ORDER = ["greenhouse", "lever", "smartrecruiters", "ashby", "recruitee", "workable"]


def _slug_candidates(company):
    """Variantes de slug à essayer (le nom exact dans l'URL de l'ATS varie :
    minuscules sans espaces, avec tirets, casse d'origine pour SmartRecruiters/Ashby)."""
    cands, seen = [], set()

    def add(s):
        if s and s not in seen:
            seen.add(s)
            cands.append(s)

    add(company.get("slug"))
    for extra in company.get("slugs", []):
        add(extra)
    name = company["name"]
    add(name.lower().replace(" ", ""))
    add(name.lower().replace(" ", "-"))
    add(name.replace(" ", ""))            # casse d'origine (SmartRecruiters, Ashby)
    add(_strip_accents(name).lower().replace(" ", ""))
    return cands


def fetch_career_sites():
    """Offres issues des sites carrière (ATS) des entreprises surveillées. Pour
    chaque entreprise, essaie chaque ATS (Greenhouse/Lever/SmartRecruiters/Ashby/
    Recruitee/Workable) avec plusieurs variantes de slug, jusqu'à trouver des
    offres. Best-effort ; réseau ouvert requis (GitHub Actions)."""
    print("  → Sites carrière (ATS)...")
    all_jobs, unresolved = [], []
    for company in _CAREER_COMPANIES:
        order = [company["ats"]] if company.get("ats") else _ATS_ORDER
        cands = _slug_candidates(company)
        found = None  # (ats, slug, raw)
        for ats in order:
            for slug in cands:
                try:
                    res = _ATS_FETCHERS[ats](slug, company["name"])
                except Exception:
                    res = None
                if res:
                    found = (ats, slug, res)
                    break
            if found:
                break
            time.sleep(0.1)
        if not found:
            unresolved.append(company["name"])
            continue
        ats, slug, raw = found
        kept = [j for j in raw if is_relevant(j) and _career_location_ok(j.get("location", ""))]
        tag = ats if slug == company.get("slug") else f"{ats}:{slug}"
        print(f"     {company['name']} [{tag}] : {len(kept)} offre(s) CRM/mkt (sur {len(raw)})")
        all_jobs.extend(kept)
    if unresolved:
        print(f"     Non résolues (aucun ATS public) : {', '.join(unresolved)}")
    unique = _dedup(all_jobs)
    print(f"     {len(unique)} offres pertinentes (sites carrière)")
    return unique


# ── Enrichissement ─────────────────────────────────────────────────────────────

_geocode_cache = {}


def geocode(address):
    """Adresse -> (lon, lat) via la Base Adresse Nationale (gratuit, sans clé)."""
    if not address:
        return None
    if address in _geocode_cache:
        return _geocode_cache[address]
    coords = None
    try:
        r = requests.get("https://api-adresse.data.gouv.fr/search/",
                         params={"q": address, "limit": 1}, timeout=10)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"]
            coords = (lon, lat)
    except Exception as ex:
        print(f"     Geocode error ({address[:30]}) : {ex}")
    _geocode_cache[address] = coords
    return coords


def _next_weekday_9am():
    """Prochain jour ouvré à 9h, format Navitia (YYYYMMDDTHHMMSS)."""
    d = datetime.now() + timedelta(days=1)
    while d.weekday() >= 5:  # samedi / dimanche
        d += timedelta(days=1)
    return d.replace(hour=9, minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H%M%S")


def _navitia_journey(base_url, headers, destination, label):
    """Appel commun aux API Navitia (Navitia.io ou IDFM PRIM, même format)."""
    origin = geocode(CONFIG.get("home_address", ""))
    dest = geocode(destination + ", Île-de-France, France")
    if not origin or not dest:
        return None
    try:
        r = requests.get(
            base_url,
            params={"from": f"{origin[0]};{origin[1]}",
                    "to": f"{dest[0]};{dest[1]}",
                    "datetime": _next_weekday_9am(),
                    "datetime_represents": "arrival",
                    "count": 1},
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        journeys = r.json().get("journeys", [])
        if journeys:
            return round(journeys[0]["duration"] / 60)
    except Exception as ex:
        print(f"     {label} error ({destination[:30]}) : {ex}")
    return None


def get_commute_time_idfm(destination):
    """Trajet en transports via l'API PRIM d'Île-de-France Mobilités (gratuit).
    Basée sur Navitia — inscription sur prim.iledefrance-mobilites.fr."""
    token = CONFIG.get("idfm_token", "")
    if not token or "VOTRE" in token or not destination:
        return None
    return _navitia_journey(
        "https://prim.iledefrance-mobilites.fr/marketplace/v2/navitia/journeys",
        {"apikey": token}, destination, "IDFM")


def get_commute_time_navitia(destination):
    """Trajet via Navitia.io (payant depuis 2024 — conservé en option)."""
    token = CONFIG.get("navitia_token", "")
    if not token or "VOTRE" in token or not destination:
        return None
    return _navitia_journey(
        "https://api.navitia.io/v1/journeys",
        {"Authorization": token}, destination, "Navitia")


def get_commute_time_google(destination):
    key = CONFIG.get("google_maps_api_key", "")
    if not key or "VOTRE" in key or not destination:
        return None
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/distancematrix/json",
            params={"origins": CONFIG["home_address"],
                    "destinations": destination + ", Île-de-France, France",
                    "mode": "transit", "key": key, "language": "fr"},
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json().get("rows", [])
        if rows:
            elem = rows[0].get("elements", [])
            if elem and elem[0].get("status") == "OK":
                return round(elem[0]["duration"]["value"] / 60)
    except Exception as ex:
        print(f"     Maps error : {ex}")
    return None


def _has(key):
    v = CONFIG.get(key)
    return bool(v) and "VOTRE" not in v


def get_commute_time(destination):
    """Dispatcher : IDFM PRIM (gratuit, IDF) par défaut, puis Navitia, puis Google."""
    provider = CONFIG.get("commute_provider", "").lower()
    if not provider:
        if _has("idfm_token"):
            provider = "idfm"
        elif _has("navitia_token"):
            provider = "navitia"
        elif _has("google_maps_api_key"):
            provider = "google"
    if provider == "idfm":
        return get_commute_time_idfm(destination)
    if provider == "navitia":
        return get_commute_time_navitia(destination)
    if provider == "google":
        return get_commute_time_google(destination)
    return None


def fetch_full_text(url):
    """Récupère le texte brut d'une page d'annonce (pour retrouver le télétravail
    absent des descriptions tronquées d'Adzuna). Best-effort, tolérant aux erreurs."""
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=10, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; JobScraper/1.0)"})
        if r.status_code != 200:
            return ""
        html = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', r.text)
        text = re.sub(r'(?s)<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)[:20000]
        # Coupe les sections « offres similaires / recommandées » : leur contenu
        # (autres annonces) fausse la détection du télétravail et du salaire.
        return _trim_related(text)
    except Exception:
        return ""


def _link_dead(url):
    """Vraie si le lien pointe une offre supprimée/close (404/410, ou message
    « soft 404 » d'un ATS type Lever/Greenhouse). Conservateur : en cas de doute
    (timeout, erreur réseau), renvoie False pour ne pas supprimer à tort."""
    if not url:
        return False
    try:
        r = requests.get(url, timeout=8, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; JobScraper/1.0)"})
        if r.status_code in (404, 410):
            return True
        if r.status_code == 200 and _EXPIRED_RE.search(r.text[:20000]):
            return True
    except Exception:
        return False
    return False


# Frontières des blocs « autres offres » / pieds de page sur les pages d'annonces.
_RELATED_BOUNDARY = re.compile(
    r'postes?\s+similaires|emplois?\s+similaires|offres?\s+similaires|'
    r'recevez des offres|ces offres pourraient|vous pourriez aussi|autres offres|'
    r'similar jobs|related jobs|more jobs|recommended jobs|'
    r'type de contrat\s+cdd\s+cdi|mini\s*job\s+allemand', re.I)

# Marqueurs indiquant qu'une annonce n'est plus en ligne (offre expirée / pourvue).
# Détectés sur le texte complet de la page ; l'offre est alors écartée.
_EXPIRED_RE = re.compile(
    r"n['’’]est plus disponible|n['’’]est plus en ligne|"
    r"offre (?:expir|pourvue|clôtur|cloturee|close|termin)|cette offre a expir|"
    r"annonce (?:expir|clôtur|cloturee|d[ée]sactiv|supprim|retir)|"
    r"poste (?:d[ée]j[àa] )?pourvu|recrutement (?:est )?(?:clos|termin)|"
    r"candidatures (?:sont )?(?:clôtur|cloturee|clos)|"
    r"no longer (?:available|accepting|active)|position (?:has been )?filled|"
    r"this (?:job|position|offer) (?:is )?(?:no longer|has expired|closed)|"
    # Pages ATS supprimées (Lever/Greenhouse/…) : messages 404 « soft ».
    r"couldn.t find anything here|couldn.t find anything|"
    r"(?:job|position|posting|role)\b.{0,30}?\b(?:closed|removed|expired|"
    r"has been filled|no longer (?:available|active|open))|"
    r"this posting is no longer", re.I)


def _trim_related(text):
    m = _RELATED_BOUNDARY.search(text or "")
    return text[:m.start()] if m else text


def extract_salary(text):
    for p in [r'\d{2,3}[\s ]?\d{3}\s*[€k]\s*(?:brut|annuel|/an)?',
              r'\d{2,3}[Kk]\s*[€]?\s*[-–]\s*\d{2,3}[Kk]',
              r'\d{2,3}[\s ]?\d{3}\s*[-–]\s*\d{2,3}[\s ]?\d{3}\s*€']:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return None


def extract_telework_days(text):
    """Nombre de jours de télétravail/semaine déduit du texte.
    5 = 100 % télétravail, 0 = présentiel confirmé, None = inconnu."""
    if not text:
        return None
    t = text.lower()
    # 100 % / full remote
    if re.search(r'full[\s-]*remote|t[eé]l[eé]travail\s*(?:total|complet|100\s*%|int[eé]gral)|'
                 r'100\s*%\s*t[eé]l[eé]travail|full[\s-]*t[eé]l[eé]travail|'
                 r'enti[eè]rement\s+[àa]\s+distance|remote\s*first', t):
        return 5
    # "X jours de télétravail" / "X j / semaine de télétravail"
    m = re.search(r'(\d)\s*(?:jours?|j)\s*(?:\/|par\s*)?(?:semaine)?\s*(?:de\s*)?t[eé]l[eé]travail', t)
    if m:
        return int(m.group(1))
    m = re.search(r't[eé]l[eé]travail\s*[:\-]?\s*(\d)\s*(?:jours?|j)', t)
    if m:
        return int(m.group(1))
    # fourchette "2 à 3 jours de télétravail" → borne haute
    m = re.search(r'\d\s*[àa]\s*(\d)\s*jours?\s*(?:de\s*)?t[eé]l[eé]travail', t)
    if m:
        return int(m.group(1))
    # "X jours sur site / présentiel" → 5 - X (semaine de 5 jours)
    m = re.search(r'(\d)\s*jours?\s*(?:sur\s*site|de\s*pr[eé]sentiel|au\s*bureau|en\s*pr[eé]sentiel)', t)
    if m:
        return max(0, 5 - int(m.group(1)))
    # présentiel explicite
    if re.search(r'pas\s+de\s+t[eé]l[eé]travail|100\s*%\s*pr[eé]sentiel|'
                 r'sans\s+t[eé]l[eé]travail|uniquement\s+en\s+pr[eé]sentiel', t):
        return 0
    return None


_FOREIGN_MARKERS = [
    "belgi", "luxembourg", "suisse", "switzerland", "espagne", "spain",
    "allemagne", "germany", "royaume-uni", "london", "londres", "portugal",
    "maroc", "tunisie", "italie", "italy", "pays-bas", "netherlands",
]


def is_in_france(location, description=""):
    """Heuristique : les sources étant françaises, on renvoie True par défaut
    et False seulement si un marqueur étranger apparaît dans le lieu."""
    t = (location or "").lower()
    return not any(m in t for m in _FOREIGN_MARKERS)


def parse_salary_value(s):
    if not s:
        return None
    nums = re.findall(r'\d{2,3}[\s ]?\d{3}', s.replace(" ", ""))
    if nums:
        try:
            return int(nums[0].replace(" ", "").replace(" ", ""))
        except Exception:
            pass
    m = re.search(r'(\d{2,3})[Kk]', s)
    if m:
        return int(m.group(1)) * 1000
    return None


def check_cdi(text):
    return bool(re.search(r'\bCDI\b', text, re.IGNORECASE))


def check_salesforce_mandatory(text):
    for p in CONFIG["salesforce_mandatory_patterns"]:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False


# ── Scoring ────────────────────────────────────────────────────────────────────

def compute_score(job):
    score = 5
    reasons = []
    text = ((job.get("title") or "") + " " +
            (job.get("description") or "") + " " +
            (job.get("company") or "")).lower()

    expert = [t for t in CANDIDATE_PROFILE["tools_expert"] if t in text]
    if len(expert) >= 3:
        score += 2
        reasons.append(f"Outils clés : {', '.join(expert[:4])}")
    elif expert:
        score += 1
        reasons.append(f"Outils : {', '.join(expert[:3])}")

    skills = [s for s in CANDIDATE_PROFILE["skills"] if s in text]
    if len(skills) >= 3:
        score += 1
        reasons.append(f"Compétences alignées ({len(skills)})")

    pref = [s for s in CONFIG["preferred_sectors"] if s in text]
    if pref:
        score += 1
        reasons.append(f"Secteur favorable : {pref[0]}")

    penal = [s for s in CONFIG["penalized_sectors"] if s in text]
    if penal:
        score -= 2
        reasons.append(f"Secteur pénalisé : {penal[0]}")

    # Pondérer selon la part réelle de CRM lifecycle (§2/§16) : bonus si signaux
    # email/segmentation/campagne/automation ; malus si l'offre est surtout
    # orientée acquisition/growth (nouveaux prospects) sans lifecycle.
    has_lifecycle = bool(_MARKETING_SIGNALS.search(text))
    if has_lifecycle:
        score += 1
        reasons.append("Signaux CRM lifecycle (email / segmentation / campagne)")
    if _ACQUISITION_BODY.search(text) and not has_lifecycle:
        score -= 2
        reasons.append("Orientation acquisition / growth (hors lifecycle)")

    if check_salesforce_mandatory(text):
        score -= 2
        reasons.append("⚠️ Salesforce obligatoire")

    sal = parse_salary_value(job.get("salary_raw") or job.get("salary_extracted") or "")
    if sal:
        if sal < CONFIG["salary_hard_min"]:
            score -= 2
            reasons.append(f"Salaire sous ton actuel ({sal:,}€)")
        elif CONFIG["salary_target_min"] <= sal <= CONFIG["salary_target_max"] + 5000:
            score += 1
            reasons.append(f"Salaire dans la cible ({sal:,}€)")

    commute = job.get("commute_minutes")
    if commute:
        if commute > 75:
            score -= 1
            reasons.append(f"Trajet long ({commute} min)")
        elif commute <= 45:
            score += 1
            reasons.append(f"Trajet court ({commute} min)")

    tw = job.get("telework_days")
    if tw and tw >= 2:
        score += 1
        reasons.append(f"Télétravail {tw}j/sem")

    return max(1, min(10, score)), reasons


# Signal CRM/marketing fort dans le TITRE (pour la reco « à postuler »).
_CORE_CRM_TITLE = re.compile(
    r'\bcrm\b|campaign manager|marketing automation|lifecycle|email marketing|'
    r'marketing relationnel|fid[ée]lisation|r[ée]tention|chef de projet crm|'
    r'responsable crm|crm manager|marketing crm|chargé[e]?\s+de\s+campagnes?', re.I)
# Intitulés hors profil qui, même avec « CRM » dans le titre, ne sont pas cible.
_OFFPROFILE_TITLE = re.compile(
    r'product manager|accounting|\boperations?\b|\bsupport\b|\bsales\b|recruit|'
    r'data (?:engineer|analyst|scientist)|développeur|developer', re.I)


def recommend_offer(job, score=None):
    """Robot de tri (règles) : renvoie (recommandation, raison).
    recommandation ∈ {à_postuler, à_revoir, à_écarter}. Basé sur le score,
    les alertes (flags), le trajet / télétravail et le salaire. L'IA pourra
    affiner ce jugement plus tard (mêmes champs de sortie)."""
    if score is None:
        score = compute_score(job)[0]
    flags = job.get("flags") or []
    tw = job.get("telework_days")
    cm = job.get("commute_minutes")
    remote_france = isinstance(tw, int) and tw >= 4 and job.get("in_france", True)
    commute_ok = isinstance(cm, (int, float)) and cm <= 75
    commute_unknown = cm in (None, "", 0)
    criteria_ok = remote_france or commute_ok or commute_unknown

    # À écarter (l'offre passe le filtre dur mais présente peu d'intérêt).
    if isinstance(cm, (int, float)) and cm > 90 and not remote_france:
        return "à_écarter", f"Trajet {int(cm)} min et pas 100 % télétravail"
    if score <= 3:
        return "à_écarter", f"Score faible ({score}/10)"

    # À postuler : bon score, aucun signal à vérifier, critère trajet/TT respecté,
    # ET un vrai signal CRM dans le titre sans intitulé hors profil.
    title = job.get("title") or ""
    strong_title = _CORE_CRM_TITLE.search(title) and not _OFFPROFILE_TITLE.search(title)
    if not flags and score >= 6 and criteria_ok and strong_title:
        bits = []
        if remote_france:
            bits.append("100 % télétravail France")
        elif commute_ok:
            bits.append(f"trajet {int(cm)} min")
        bits.append(f"score {score}/10")
        return "à_postuler", "Profil correspondant, aucun signal à vérifier (" + ", ".join(bits) + ")"

    # À revoir (ambigu : on explique pourquoi).
    if flags:
        return "à_revoir", "À vérifier : " + " ; ".join(flags[:3])
    if not criteria_ok and isinstance(cm, (int, float)):
        return "à_revoir", f"Trajet {int(cm)} min, hors critère (≤75 min ou 100 % télétravail)"
    if not strong_title:
        return "à_revoir", "À confirmer : titre sans signal CRM explicite"
    return "à_revoir", f"Correct, à confirmer (score {score}/10)"


# ── Filtre ─────────────────────────────────────────────────────────────────────

def should_include(job):
    # Le filtrage trajet / télétravail est délégué au dashboard (filtre
    # "Mes critères", activé par défaut) pour ne perdre aucune offre à la source
    # — notamment les postes 100 % télétravail éloignés.
    if not is_relevant(job):
        return False, "Hors périmètre CRM / Campaign Manager"

    exclude, reason, _ = screen_offer(job)
    if exclude:
        return False, reason

    sal = parse_salary_value(job.get("salary_raw") or job.get("salary_extracted") or "")
    if sal and sal < CONFIG["salary_hard_min"]:
        return False, f"Salaire sous actuel ({sal:,}€)"

    return True, "OK"


# ── Google Sheets ──────────────────────────────────────────────────────────────

def write_to_sheets(jobs):
    sid = CONFIG.get("spreadsheet_id", "")
    if not sid or "VOTRE" in sid:
        print("[Sheets] Non configuré — ignoré")
        return
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_service_account_file(
            CONFIG["google_credentials_file"],
            scopes=["https://www.googleapis.com/auth/spreadsheets"])
        svc = build("sheets", "v4", credentials=creds).spreadsheets()

        headers = ["Date scan", "Plateforme", "Titre", "Entreprise", "Localisation",
                   "Lien", "Trajet (min)", "Télétravail (j/sem)", "Salaire brut annuel",
                   "CDI", "Note /10", "Points clés", "Description courte"]
        rows = [headers]
        for job in jobs:
            score, reasons = compute_score(job)
            full = (job.get("title", "") + " " + job.get("description", "")).lower()
            rows.append([
                datetime.now().strftime("%d/%m/%Y"),
                job.get("source", ""), job.get("title", ""), job.get("company", ""),
                job.get("location", ""), job.get("link", ""),
                job.get("commute_minutes", "N/A"), job.get("telework_days", "N/C"),
                job.get("salary_raw") or job.get("salary_extracted") or "N/C",
                "✓ CDI" if check_cdi(full) else "À vérifier",
                score, " | ".join(reasons),
                (job.get("description") or "")[:300].replace("\n", " "),
            ])
        svc.values().update(spreadsheetId=sid, range=f"{CONFIG['sheet_name']}!A1",
                            valueInputOption="RAW", body={"values": rows}).execute()
        print(f"✓ {len(jobs)} offres dans Google Sheets")
    except Exception as ex:
        print(f"[Sheets] Erreur : {ex}")


# ── Email ──────────────────────────────────────────────────────────────────────

def send_email_recap(jobs):
    if not CONFIG.get("email_enabled") or "VOTRE" in CONFIG.get("email_sender", "VOTRE"):
        print("[Email] Non configuré — ignoré")
        return

    scored = sorted([(job, *compute_score(job)) for job in jobs], key=lambda x: x[1], reverse=True)
    top = scored[:10]
    date_str = datetime.now().strftime("%d/%m/%Y")

    rows_html = ""
    for job, score, reasons in top:
        color = "#2e7d32" if score >= 7 else "#f57c00" if score >= 5 else "#c62828"
        sf_warn = "<br><b style='color:#e65100'>⚠️ Salesforce obligatoire</b>" \
            if check_salesforce_mandatory((job.get("description") or "").lower()) else ""
        rows_html += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #eee;">
            <b><a href="{job.get('link','#')}" style="color:#1a73e8;">{job.get('title','N/A')}</a></b><br>
            <span style="color:#555">{job.get('company','N/C')} — {job.get('location','N/C')}</span><br>
            <small style="color:#888">
              Trajet : {job.get('commute_minutes','N/A')} min &nbsp;|&nbsp;
              Télétravail : {job.get('telework_days','N/C')}j &nbsp;|&nbsp;
              Salaire : {job.get('salary_raw') or job.get('salary_extracted') or 'N/C'} &nbsp;|&nbsp;
              {job.get('source','')}
            </small>{sf_warn}<br>
            <small style="color:#666">{' | '.join(reasons)}</small>
          </td>
          <td style="padding:10px;border-bottom:1px solid #eee;text-align:center;vertical-align:middle;width:60px;">
            <b style="font-size:20px;color:{color}">{score}/10</b>
          </td>
        </tr>"""

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;color:#333">
      <h2 style="color:#1a73e8">📋 Offres CRM — {date_str}</h2>
      <p>{len(jobs)} offres retenues · Top {len(top)} affichées</p>
      <table style="width:100%;border-collapse:collapse;margin-top:16px">
        <thead><tr style="background:#f5f5f5">
          <th style="padding:10px;text-align:left">Offre</th>
          <th style="padding:10px;width:60px">Note</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="margin-top:20px;font-size:11px;color:#aaa">Script automatique — 5h00 chaque matin</p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Job Scraper] {len(jobs)} offres CRM — {date_str}"
    msg["From"] = CONFIG["email_sender"]
    msg["To"] = CONFIG["email_recipient"]
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(CONFIG["email_smtp_server"], CONFIG["email_smtp_port"]) as srv:
            srv.starttls()
            srv.login(CONFIG["email_sender"], CONFIG["email_password"])
            srv.sendmail(CONFIG["email_sender"], CONFIG["email_recipient"], msg.as_string())
        print(f"✓ Email envoyé à {CONFIG['email_recipient']}")
    except Exception as ex:
        print(f"[Email] Erreur : {ex}")


# ── Utils ──────────────────────────────────────────────────────────────────────

def _job_completeness(j):
    """Score de « richesse » d'une offre, pour garder la meilleure d'un doublon."""
    s = 0.0
    if j.get("company"):
        s += 2
    if j.get("salary_raw") or j.get("salary_extracted"):
        s += 1
    if j.get("location"):
        s += 0.5
    s += min(len(j.get("description") or ""), 600) / 600.0
    return s


def _title_match(a, b):
    """Deux titres normalisés désignent la même offre : soit identiques, soit
    l'un est le début de l'autre (une plateforme ajoute un descriptif, ex.
    « … CRM & Clienteling » vs « … CRM & Clienteling Maison de Luxe »). Le seuil
    de 24 caractères évite de fusionner des titres génériques (« crm manager »)."""
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 24 and longer.startswith(shorter)


def _dedup(jobs):
    """Dédoublonne y compris entre plateformes : titre normalisé (ignore H/F,
    (F/H), ponctuation, casse) + entreprise souple. Une même offre diffusée sur
    Adzuna / France Travail / e-mail = une seule ligne (la plus complète)."""
    kept = []  # chaque élément : {"job":..., "tk":..., "cn":..., "loc":..., "src":...}
    for j in jobs:
        tk = _norm_txt(j.get("title", ""))[:60]
        cn = _norm_txt(j.get("company", ""))
        loc = _norm_txt(j.get("location", ""))
        src = (j.get("source") or "").strip().lower()
        day = (str(j.get("published") or "")[:10])  # AAAA-MM-JJ
        hit = None
        for u in kept:
            if not _title_match(tk, u["tk"]):
                continue
            same_company = cn and u["cn"] and (cn in u["cn"] or u["cn"] in cn)
            # Entreprise absente d'un côté : on ne fusionne que si le titre est
            # assez spécifique (≥ 20 car.), pour éviter d'écraser des postes
            # homonymes distincts (« Chef de projet CRM », etc.).
            empty_ok = (not cn or not u["cn"]) and len(tk) >= 20
            # Même recruteur sous deux libellés différents (fréquent sur Adzuna :
            # « OpenSourcing » vs « osgroupeopensuccess ») : titre normalisé
            # identique + même lieu + même source + MÊME JOUR de publication +
            # titre assez spécifique. Le même jour évite de fusionner deux postes
            # homonymes distincts publiés à des dates différentes.
            same_recruiter = (tk == u["tk"] and len(tk) >= 14 and loc and loc == u["loc"]
                              and src and src == u["src"] and day and day == u["day"])
            if same_company or empty_ok or same_recruiter:
                hit = u
                break
        if hit is None:
            kept.append({"job": j, "tk": tk, "cn": cn, "loc": loc, "src": src, "day": day})
        elif _job_completeness(j) > _job_completeness(hit["job"]):
            # garde la version la plus complète
            hit["job"], hit["cn"], hit["loc"], hit["day"] = j, cn, loc, day
    return [u["job"] for u in kept]


def export_json_local(jobs, path="jobs_output.json"):
    out = []
    for j in jobs:
        score, reasons = compute_score(j)
        reco, reco_reason = recommend_offer(j, score)
        out.append({**j, "score": score, "score_reasons": reasons,
                    "recommendation": reco, "recommendation_reason": reco_reason})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"✓ JSON : {path} ({len(out)} offres)")
    from collections import Counter
    c = Counter(o["recommendation"] for o in out)
    print(f"  Recommandations : à postuler {c.get('à_postuler',0)}, "
          f"à revoir {c.get('à_revoir',0)}, à écarter {c.get('à_écarter',0)}")


# ── Suivi des candidatures par email (robot) ───────────────────────────────────
#
# Lit heloise.emploi (IMAP), détecte les emails de candidature et leur statut
# (accusé de réception -> en_attente ; refus -> negatif ; entretien -> positif),
# puis met à jour le champ `auto` de chaque candidature dans le dépôt PRIVÉ
# recherche-emploi-candidatures via l'API GitHub (secret CANDIDATURES_TOKEN).
# Best-effort : à affiner avec de vrais emails de réponse.

def _strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '')
                   if unicodedata.category(c) != 'Mn')


def _norm_txt(x):
    x = _strip_accents((x or '').lower())
    x = re.sub(r'\(.*?\)|\bh/?f\b|\bf/?h\b|\bm/?f\b|\bcdi\b|\bcdd\b', ' ', x)
    return re.sub(r'[^a-z0-9]+', ' ', x).strip()


def _title_key(title):
    return _norm_txt(title)[:40]


def _company_loose(a, b):
    """Entreprises « proches » : l'une contient l'autre (Aravati ⊂ Aravati France)."""
    a, b = _norm_txt(a), _norm_txt(b)
    if not a or not b:
        return True
    return a in b or b in a


def _same_offer(c1, t1, c2, t2):
    """Même offre (même titre normalisé + entreprise proche) — dédoublonne les
    annonces d'une même offre diffusée sur plusieurs plateformes."""
    return _title_key(t1) == _title_key(t2) and _company_loose(c1, c2)


_APP_CONFIRM = re.compile(
    r'bien re[çc]u (?:ta|votre|ton) candidature|(?:ta|votre) candidature a bien|'
    r'accus[ée] de r[ée]ception|candidature (?:bien )?(?:re[çc]ue|enregistr)|'
    r'nous avons (?:bien )?re[çc]u|confirmons la bonne r[ée]ception|'
    r'we(?:\'ve| have) received your application|thank you for (?:your )?appl(?:ying|ication)|'
    r'application (?:has been )?received|(?:ta|votre) candidature (?:au poste|pour)|'
    r'(?:nous (?:vous |te )?)?remerci\w+ (?:pour|de)|'
    r'merci (?:beaucoup |infiniment |sinc[èe]rement )?(?:pour|de)\s+(?:votre|ta|ton|l\'int[ée]r[êe]t|nous avoir)|'
    r'reviendrons vers (?:vous|toi)|revenons vers (?:vous|toi)|'
    r'(?:nous |l\'|nous l\')?[ée]tudions|[ée]tudier(?:ons)?\s+(?:avec attention |attentivement )?'
    r'(?:votre|ta)|examin(?:ons|erons|er)\s+(?:attentivement\s+)?(?:votre|ta)', re.I)
# Refus « durs » : formulations non ambiguës de non-retenue.
_APP_NEG_HARD = re.compile(
    r'ne (?:donnons|donne|donnerons) pas suite|nous ne retenons pas|'
    r'not (?:be )?(?:moving forward|to proceed|selected)|another candidate|'
    r'd[ée]cid[ée] de ne pas donner suite|ne pas retenir votre candidature|'
    r'candidature n\'a pas [ée]t[ée] retenue', re.I)
# Refus « souples » : ces tournures apparaissent AUSSI dans des accusés de
# réception conditionnels (« si sans nouvelle d\'ici 3 semaines, considérez que
# votre candidature n\'a pas été retenue »). On ne les compte comme refus que
# hors contexte conditionnel.
_APP_NEG_SOFT = re.compile(
    r'n\'a(?:vons)? pas (?:[ée]t[ée] )?retenue?|pas retenu votre|regret(?:tons)?|'
    r'malheureusement|unfortunately|autre candidat|not retained|'
    r'ne correspond(?:ait)? pas (?:au profil|à nos)', re.I)
# Contexte conditionnel (le refus n\'est pas acté, c\'est un accusé de réception).
_APP_COND = re.compile(
    r'si\s+(?:tu|vous)\s+n[e\']?\s*(?:re[çc]o(?:is|it|ivez)|avez|as|aviez)\s+(?:pas |plus )?'
    r'(?:de |eu de |re[çc]u de )?(?:nouvelles?|retour|r[ée]ponse)|'
    r'sans (?:nouvelle|retour|r[ée]ponse)\s+de\s+(?:notre|nos|ma)|'
    r'd\'ici\s+(?:\d+|un|une|deux|trois|quelques)\s*(?:semaines?|jours?|mois)|'
    r'pass[ée]\s+ce\s+d[ée]lai|au-del[àa]\s+de\s+ce\s+d[ée]lai|'
    r'sous\s+(?:\d+|quelques)\s*(?:semaines?|jours?|mois)|'
    r'(?:vous pouvez|tu peux)\s+consid[ée]rer|cela signifie que', re.I)
_APP_POS = re.compile(
    r'souhait\w* (?:vous )?rencontrer|(?:proposer|convier)\w* .{0,20}entretien|'
    r'planifier .{0,20}(?:entretien|appel|[ée]change)|vos disponibilit[ée]s|'
    r'invit\w* .{0,20}(?:entretien|interview)|schedule (?:a|an) (?:call|interview)|'
    r'(?:premier|prochain) entretien|entretien (?:t[ée]l[ée]phonique|physique|visio|rh)|'
    r'next step|prochaine [ée]tape|nous aimerions (?:vous )?[ée]changer', re.I)


def _label_status(raw):
    """Classe d'après les libellés Gmail (X-GM-LABELS) posés par l'utilisatrice —
    signal fiable, prioritaire sur les heuristiques de texte. `raw` = ligne
    d'en-tête renvoyée par IMAP. Renvoie (statut|"_skip", raison) ou None.
    Les accents peuvent être en UTF-7 modifié : on repère des sous-chaînes ASCII
    sûres (refus / process / confirmation / alerte)."""
    try:
        s = _strip_accents((raw or b"").decode("utf-8", "replace")).lower()
    except Exception:
        return None
    if "x-gm-labels" not in s:
        return None
    if "alerte" in s:
        return ("_skip", None)  # e-mail d'alerte d'offres, pas une réponse
    if "refus" in s:
        return ("negatif", "Libellé Gmail « Candidature refusée »")
    if "process" in s or "entretien" in s or "en cours" in s:
        return ("positif", "Libellé Gmail « Candidature en process »")
    if "confirmation" in s or "recue" in s or "recu" in s or "en attente" in s:
        return ("en_attente", "Libellé Gmail « Confirmation candidature reçue »")
    return None


def classify_application_email(subject, body):
    """Renvoie (statut, raison) ou None. Statuts : en_attente / positif / negatif.

    Les accusés de réception contiennent très souvent une clause de refus
    CONDITIONNELLE (« si sans nouvelle d'ici X semaines, considérez que votre
    candidature n'a pas été retenue »). Ce n'est PAS un refus : tant qu'il y a un
    remerciement / accusé de réception ET une clause conditionnelle, on classe
    « en attente ». Un vrai refus est inconditionnel et sans remerciement de ce type."""
    text = f"{subject}\n{body}"
    if not re.search(r'candidature|application|poste|recrut|talent|offre d\'emploi', text, re.I):
        return None
    # Un entretien proposé prime (parfois formulé avec des regrets ailleurs).
    if _APP_POS.search(text):
        return ("positif", "Proposition d'entretien détectée")
    ack = bool(_APP_CONFIRM.search(text))
    cond = bool(_APP_COND.search(text))
    # Accusé de réception à clause conditionnelle = en attente, pas refus.
    if ack and cond:
        return ("en_attente", "Accusé de réception de candidature")
    if _APP_NEG_HARD.search(text):
        return ("negatif", "Réponse négative détectée")
    # Refus souple : refus réel seulement HORS contexte conditionnel.
    if _APP_NEG_SOFT.search(text) and not cond:
        return ("negatif", "Réponse négative détectée")
    if ack:
        return ("en_attente", "Accusé de réception de candidature")
    return None


_GENERIC_SENDER = re.compile(
    r'no[- ]?reply|nepasrepondre|ne[- ]?pas[- ]?repondre|recrut|talent|career|jobs?|'
    r'hello|contact|team|\brh\b|notification|candidat|apply|\bhr\b', re.I)
_ATS_DOMAINS = {"teamtailor", "workday", "myworkday", "lever", "greenhouse", "smartrecruiters",
                "welcomekit", "welcometothejungle", "taleez", "flatchr", "softy", "icims",
                "successfactors", "recruitee", "personio", "factorial",
                "gmail", "google", "outlook", "hotmail", "yahoo"}


_ENCODED_WORD_RE = re.compile(r'=\?[^?]+\?[BbQq]\?[^?]*\?=')


def _decode_word(m):
    try:
        parts = email.header.decode_header(m.group(0))
        return "".join(t.decode(e or "utf-8", "replace") if isinstance(t, (bytes, bytearray))
                       else t for t, e in parts)
    except Exception:
        return m.group(0)


def _decode_mime(raw):
    """Décode les mots-encodés MIME (=?UTF-8?B?…?=) d'un en-tête, en laissant le
    reste du texte intact. Robuste au contenu mixte (texte déjà décodé + mot
    encodé), là où make_header échoue. Colle d'abord les mots-encodés adjacents
    (RFC 2047 : l'espace entre eux est ignoré)."""
    if not raw:
        return raw or ""
    s = raw if isinstance(raw, str) else str(raw)
    if "=?" in s and "?=" in s:
        s = re.sub(r'\?=\s+=\?', '?==?', s)  # mots-encodés pliés adjacents
        s = _ENCODED_WORD_RE.sub(_decode_word, s)
    return s


def _sender_company(frm):
    """Devine l'entreprise depuis l'expéditeur (nom affiché sinon domaine).
    Le champ From peut être encodé (=?UTF-8?...?=) : on le décode d'abord."""
    frm = _decode_mime(frm or "")
    m = re.match(r'\s*"?([^"<]*?)"?\s*<([^>]+)>', frm)
    name = (m.group(1).strip() if m else "")
    addr = (m.group(2) if m else frm).strip()
    if name and not _GENERIC_SENDER.search(name):
        return name[:60]
    dom = addr.split("@")[-1].lower()
    dom = re.sub(r'^(mail|email|emails|e|smtp|send|go|jobs|careers|recruiting|apply|'
                 r'noreply|nepasrepondre|rh|notification)\.', '', dom)
    parts = dom.split(".")
    core = parts[-2] if len(parts) >= 2 else dom
    if core in _ATS_DOMAINS:
        return name[:60] if name else ""
    return core.capitalize()


# Titre de poste dans le corps (« pour le poste de … », « au poste de … »).
_POSTE_BODY_RE = re.compile(
    r'(?:pour le poste|au poste|sur le poste|poste\s+de|pour le r[ôo]le|'
    r'for the (?:position|role) of)\s+(?:de\s+|du\s+|d\'|of\s+)?'
    r'(.+?)(?=\s+[HF]/?[FH]\b|\s*[\(\.!\n]|\s*$)', re.I)
# Sujets « génériques » (accusé sans intitulé de poste) : on cherchera le poste
# dans le corps, et l'association se fera alors sur l'entreprise.
_GENERIC_APP_TITLE = re.compile(
    # Sujet d'email générique / accusé de réception (pas un intitulé de poste) :
    # sert à rapprocher un email de la carte existante par la SEULE entreprise.
    r'^\s*(?:re\s*:|fwd?\s*:|tr\s*:)?\s*(?:merci (?:pour|de)\s+)?(?:ta|ton|votre|your)?\s*'
    r'(?:candidature|application)\b(?!\s+(?:pour|au|de|for))|'
    r'(?:votre|ta|ton|your)\s+(?:candidature|application)\b|'
    r'nous avons (?:bien )?re[çc]u|bien re[çc]u (?:ta|votre|ton) candidature|'
    r'candidature (?:bien )?(?:re[çc]ue|enregistr)|accus[ée] de r[ée]ception|'
    r'confirmation (?:de )?(?:candidature|r[ée]ception)|merci (?:pour|de) (?:ta|votre|nous)|'
    r'thank you for (?:your )?appl|we(?:\'ve| have) received your|application received|'
    r'\bbienvenue\b|welcome to', re.I)


def _company_from_subject(subject):
    """Entreprise mentionnée dans le sujet (« … chez X », « … at X »)."""
    m = re.search(r'\b(?:chez|at)\s+([^!.\n]+?)\s*[!.…]*\s*$', subject or "", re.I)
    if m:
        c = m.group(1).strip(' "\'')
        if 2 <= len(c) <= 60:
            return c
    return ""


def _app_same(ca, ta, cb, tb):
    """Même candidature. Titre proche + entreprise proche ; si l'un des titres est
    générique (« Votre candidature »), on associe sur la seule entreprise — pour
    rattacher une réponse email à une candidature existante."""
    if _GENERIC_APP_TITLE.search(ta or "") or _GENERIC_APP_TITLE.search(tb or ""):
        return bool(_norm_txt(ca)) and bool(_norm_txt(cb)) and _company_loose(ca, cb)
    return _same_offer(ca, ta, cb, tb)


def _extract_offer_title(subject, body=""):
    # Le sujet peut être plié sur plusieurs lignes : on aplatit les espaces.
    s = re.sub(r'\s+', ' ', _decode_mime(subject)).strip()
    m = re.search(r'(?:pour le poste|au poste|for the (?:position|role) of)\s+(?:de\s+|du\s+|d\')?(.+)$', s, re.I)
    if m:
        return m.group(1).strip(' "\'').strip()[:80]
    m = re.search(r'(?:candidature|application)[^:\-–—]*[:\-–—]\s*(.+)$', s, re.I)
    if m:
        return m.group(1).strip(' "\'').strip()[:80]
    # Sujet générique (« Votre candidature ») : on tente d'extraire l'intitulé
    # du poste depuis le corps, pour mieux associer à une candidature existante.
    if body and _GENERIC_APP_TITLE.search(s):
        mb = _POSTE_BODY_RE.search(re.sub(r'\s+', ' ', body))
        if mb:
            t = mb.group(1).strip(' "\'').strip()
            if 3 <= len(t) <= 80:
                return t
    return s[:80]


def _gmail_all_mail(imap):
    """Nom du dossier « Tous les messages » de Gmail (flag spécial \\All),
    quelle que soit la langue du compte. None si introuvable -> INBOX en repli."""
    try:
        typ, boxes = imap.list()
        if typ != "OK":
            return None
        for b in boxes or []:
            s = b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b)
            if "\\All" in s:
                m = re.search(r'"([^"]+)"\s*$', s)
                if m:
                    return m.group(1)
                parts = s.split()
                if parts:
                    return parts[-1].strip('"')
    except Exception:
        pass
    return None


def fetch_application_emails():
    """Lit la boîte et renvoie les événements de candidature détectés."""
    address = CONFIG.get("gmail_address", "")
    password = CONFIG.get("gmail_app_password", "")
    if not address or not password:
        print("  → Gmail non configuré — suivi ignoré")
        return []
    alert_senders = {s for cfg in _EMAIL_ALERT_SOURCES for s in cfg["senders"]}
    lookback = int(CONFIG.get("tracking_lookback_days", 30))
    since = (datetime.now() - timedelta(days=lookback)).strftime("%d-%b-%Y")
    events = []
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        imap.login(address, password)
        # Lit « Tous les messages » (All Mail) et non seulement INBOX : les emails
        # de réponse sont souvent archivés dans un libellé (donc hors INBOX). On
        # les retrouve ainsi, ce qui permet d'appliquer leur statut/libellé.
        mailbox = _gmail_all_mail(imap) or "INBOX"
        imap.select(mailbox if mailbox == "INBOX" else f'"{mailbox}"', readonly=True)
        typ, data = imap.search(None, "SINCE", since)
        uids = data[0].split() if (typ == "OK" and data and data[0]) else []
        for uid in uids[-400:]:
            try:
                typ, md = imap.fetch(uid, "(RFC822)")
                if typ != "OK" or not md or not md[0]:
                    continue
                msg = email.message_from_bytes(md[0][1])
                frm_raw = str(msg.get("From", ""))
                if any(s in frm_raw.lower() for s in alert_senders):
                    continue  # ignore les emails d'alerte d'offres
                # Libellés Gmail (X-GM-LABELS) posés par l'utilisatrice : lus dans
                # un fetch séparé et défensif — s'ils priment sur l'analyse texte,
                # ils ne doivent JAMAIS casser la détection si l'extension échoue.
                lab = None
                try:
                    lt, ld = imap.fetch(uid, "(X-GM-LABELS)")
                    if lt == "OK" and ld:
                        raw = b" ".join(x for x in ld if isinstance(x, (bytes, bytearray)))
                        lab = _label_status(raw)
                except Exception:
                    lab = None
                if lab and lab[0] == "_skip":
                    continue  # libellé « Alertes » : pas une réponse de candidature
                subject = _decode_mime(msg.get("Subject", ""))
                html, text = _email_body_html(msg)
                body = re.sub(r'<[^>]+>', ' ', html) if html else text
                cls = lab or classify_application_email(subject, body)
                if not cls:
                    continue
                status, reason = cls
                try:
                    date = int(email.utils.parsedate_to_datetime(msg.get("Date", "")).timestamp() * 1000)
                except Exception:
                    date = int(time.time() * 1000)
                company = _company_from_subject(subject) or _sender_company(frm_raw)
                events.append({"company": company,
                               "title": _extract_offer_title(subject, body),
                               "status": status, "reason": reason, "date": date})
            except Exception as ex:
                print(f"     lecture mail suivi : {ex}")
        try:
            imap.close()
        except Exception:
            pass
        imap.logout()
    except Exception as ex:
        print(f"  → Suivi candidatures : ERREUR IMAP {ex}")
        return []
    return events


def update_candidatures_tracking():
    """Met à jour le champ `auto` des candidatures dans le dépôt privé (API GitHub)."""
    repo = CONFIG.get("candidatures_repo", "")
    token = CONFIG.get("candidatures_token", "")
    if not repo or not token:
        print("  → Dépôt privé de candidatures non configuré — suivi ignoré")
        return
    events = fetch_application_emails()
    if not events:
        # Pas de nouvel email : on continue quand même pour RÉPARER les entrées
        # existantes au sujet encodé (=?UTF-8?...?=) laissées par une ancienne
        # version. Le nettoyage ne dépend pas d'un nouvel email.
        print("  → Aucun nouvel email de candidature — vérification/réparation")
    # Un seul événement par offre (matching souple) : le plus récent gagne
    # (une réponse remplace un accusé), et la même offre sur 2 sites = 1 seul.
    best = []
    for e in events:
        m = next((x for x in best if _app_same(x["company"], x["title"], e["company"], e["title"])), None)
        if m:
            if e["date"] > m["date"]:
                m.update(e)
        else:
            best.append(e)
    branch = CONFIG.get("candidatures_branch", "main")
    api = f"https://api.github.com/repos/{repo}/contents/candidatures.json"
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(f"{api}?ref={branch}", headers=headers, timeout=20)
        sha, cands = None, []
        if r.status_code == 200:
            meta = r.json()
            sha = meta.get("sha")
            try:
                cands = json.loads(base64.b64decode(meta.get("content", "")).decode("utf-8"))
            except Exception:
                cands = []
            if not isinstance(cands, list):
                cands = []
        elif r.status_code != 404:
            print(f"  → Suivi candidatures : GET {r.status_code}")
            return
        # Répare les entrées existantes au sujet encodé (=?UTF-8?...?= mal décodé
        # par une ancienne version) : on ne veut plus voir « =?UTF-8?B?... ».
        repaired = 0
        for c in cands:
            for k in ("title", "company"):
                v = c.get(k) or ""
                if "=?" in v and "?=" in v:
                    dv = _decode_mime(v)
                    if dv and dv != v:
                        c[k] = dv
                        repaired += 1
        col = {"en_attente": "postule", "positif": "entretien", "negatif": "reponse"}
        changed = 0
        for e in best:
            auto = {"status": e["status"], "reason": e["reason"], "date": e["date"]}
            # Match souple contre les candidatures existantes (même offre, quel
            # que soit le site / la variante d'entreprise ; entreprise seule si le
            # titre de l'email est générique).
            c = next((x for x in cands if _app_same(
                x.get("company", ""), x.get("title", ""), e["company"], e["title"])), None)
            # Le robot ne CRÉE plus de carte à partir des emails (trop d'erreurs :
            # doublons, emails de bienvenue pris pour des candidatures…). Les cartes
            # sont créées uniquement depuis le dashboard (Suivre / ajout manuel).
            # Un email ne fait que METTRE À JOUR le statut d'une carte existante.
            if not c:
                continue
            old = c.get("auto") or {}
            if old.get("status") != e["status"] or e["date"] > (old.get("date") or 0):
                c["auto"] = auto
                # Candidature créée par le robot autrefois (source email) : on
                # aligne aussi la colonne sur le statut détecté.
                if c.get("source") == "email" and col.get(e["status"]):
                    c["status"] = col[e["status"]]
                    c["updatedAt"] = max(int(c.get("updatedAt") or 0), e["date"])
                changed += 1

        # --- Fusion des cartes créées par email dans la candidature « suivie »
        # correspondante (même entreprise) : évite le doublon « confirmation de
        # réception » + « offre suivie ». L'entreprise de la carte email est
        # d'abord relue depuis le sujet (« … chez X »). ---
        merged = 0
        for ec in list(cands):
            if ec.get("deleted") or ec.get("source") != "email":
                continue
            ec_comp = _company_from_subject(ec.get("title", "")) or ec.get("company", "")
            if not _norm_txt(ec_comp):
                continue
            target = next((c for c in cands
                           if c is not ec and not c.get("deleted") and c.get("source") != "email"
                           and _norm_txt(c.get("company", ""))
                           and _company_loose(c.get("company", ""), ec_comp)), None)
            if not target:
                continue
            a = ec.get("auto")
            if a and (not target.get("auto")
                      or a.get("date", 0) >= (target.get("auto") or {}).get("date", 0)):
                target["auto"] = a
                if col.get(a.get("status")):
                    target["status"] = col[a["status"]]
            if not target.get("link") and ec.get("link"):
                target["link"] = ec["link"]
            ec["deleted"] = True
            ec["updatedAt"] = int(time.time() * 1000)
            merged += 1
            changed += 1

        if not changed and not repaired:
            print("  → Suivi candidatures : rien de nouveau")
            return
        if repaired:
            print(f"  → Suivi candidatures : {repaired} champ(s) encodé(s) réparé(s)")
        if merged:
            print(f"  → Suivi candidatures : {merged} carte(s) email fusionnée(s)")
        content = base64.b64encode(
            json.dumps(cands, ensure_ascii=False, indent=2).encode("utf-8")).decode()
        body = {"message": "MAJ suivi candidatures (robot email)", "content": content, "branch": branch}
        if sha:
            body["sha"] = sha
        pr = requests.put(api, headers=headers, json=body, timeout=20)
        if pr.status_code in (200, 201):
            print(f"  → Suivi candidatures : {changed} mise(s) à jour")
        else:
            print(f"  → Suivi candidatures : PUT {pr.status_code}")
    except Exception as ex:
        print(f"  → Suivi candidatures : ERREUR {ex}")


# ── Offres écartées (archives) : exclusion côté robot ───────────────────────────
#
# Les offres qu'Héloïse a écartées (« ✕ Pas pertinent ») sont synchronisées dans
# le dépôt PRIVÉ (archivees.json) par le dashboard. Le robot les relit et retire
# du scan toute offre correspondante — même diffusée par une autre plateforme —
# pour qu'une annonce déjà analysée et rejetée ne réapparaisse jamais.
# Le rapprochement est platform-agnostique : titre normalisé + entreprise proche
# (même logique que le dédoublonnage et que le dashboard).

def fetch_archived_offers():
    """Liste des offres écartées (actives, hors tombstones) depuis le dépôt privé."""
    repo = CONFIG.get("candidatures_repo", "")
    token = CONFIG.get("candidatures_token", "")
    if not repo or not token:
        return []
    branch = CONFIG.get("candidatures_branch", "main")
    api = f"https://api.github.com/repos/{repo}/contents/archivees.json"
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(f"{api}?ref={branch}", headers=headers, timeout=20)
        if r.status_code == 404:
            return []  # pas encore d'archives synchronisées
        if r.status_code != 200:
            print(f"  → Archives : GET {r.status_code}")
            return []
        data = json.loads(base64.b64decode(r.json().get("content", "")).decode("utf-8"))
        if not isinstance(data, list):
            return []
        return [a for a in data if isinstance(a, dict) and not a.get("deleted")]
    except Exception as ex:
        print(f"  → Archives : ERREUR {ex}")
        return []


def _is_archived_offer(job, archived):
    """L'offre correspond-elle à une entrée écartée ? On réutilise la même notion
    de « même offre » que le dédoublonnage (`_title_match` + entreprise souple) :
    si le scraper fusionnerait ces deux annonces, l'archive s'applique — quelle que
    soit la plateforme (Adzuna, Meteojob, site carrière…). Les titres génériques
    courts (« crm manager ») ne suffisent pas : il faut un titre spécifique
    (≥ 24 car. en préfixe) ou une entreprise concordante, comme pour la dédup."""
    jt = _norm_txt(job.get("title", ""))[:60]
    jc = _norm_txt(job.get("company", ""))
    if not jt:
        return False

    def _prefix(a, b, n):
        """a et b identiques, ou l'un préfixe de l'autre avec ≥ n car. partagés."""
        if not a or not b:
            return False
        if a == b:
            return True
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return len(shorter) >= n and longer.startswith(shorter)

    for a in archived:
        at = _norm_txt(a.get("title", ""))[:60]
        ac = _norm_txt(a.get("company", ""))
        same_company = jc and ac and (jc in ac or ac in jc)
        if same_company:
            # Entreprise concordante : titre identique ou préfixe ≥ 14 car. (seuil
            # déjà utilisé par la dédup pour un même recruteur). Évite d'écarter
            # d'autres postes à partir d'un titre générique (« crm manager »).
            if _prefix(jt, at, 14):
                return True
        elif (not jc or not ac) and _title_match(jt, at) and len(jt) >= 20:
            # Entreprise absente d'un côté : n'écarte que sur un titre spécifique.
            return True
        # Entreprises présentes des deux côtés mais différentes : employeur distinct
        # → offre différente, on n'écarte pas.
    return False


def drop_archived(jobs):
    """Retire du scan les offres déjà écartées par Héloïse (toutes plateformes)."""
    archived = fetch_archived_offers()
    if not archived:
        return jobs, 0
    kept = [j for j in jobs if not _is_archived_offer(j, archived)]
    return kept, len(jobs) - len(kept)


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("JOB SCRAPER — CRM Campaign Manager")
    print(f"Lancé le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")
    print("=" * 60)

    all_jobs = []

    print("\n[1/4] Indeed (RSS)...")
    for s, u in build_indeed_rss_urls():
        all_jobs.extend(fetch_rss(s, u))
        time.sleep(1)

    print("\n[2/4] Welcome to the Jungle (RSS)...")
    for s, u in build_wttj_rss_urls():
        all_jobs.extend(fetch_rss(s, u))
        time.sleep(1)

    print("\n[3] France Travail (API)...")
    all_jobs.extend(fetch_francetravail_jobs())

    print("\n[4] Adzuna (API)...")
    all_jobs.extend(fetch_adzuna_jobs())

    print("\n[5] Sources 100 % télétravail...")
    all_jobs.extend(fetch_remotive_jobs())
    all_jobs.extend(fetch_weworkremotely_jobs())
    all_jobs.extend(fetch_jobicy_jobs())
    all_jobs.extend(fetch_remoteok_jobs())

    print("\n[6] The Muse + Welcome to the Jungle...")
    all_jobs.extend(fetch_themuse_jobs())
    all_jobs.extend(fetch_wttj_jobs())

    print("\n[7] Alertes e-mail (WTJ, Indeed, HelloWork, LinkedIn, Cadremploi, Meteojob)...")
    all_jobs.extend(fetch_email_alerts())

    print("\n[7b] Sites carrière (ATS des entreprises surveillées)...")
    try:
        all_jobs.extend(fetch_career_sites())
    except Exception as ex:
        print(f"  → Sites carrière : ERREUR {ex}")

    print(f"\nTotal brut : {len(all_jobs)}")
    all_jobs = _dedup(all_jobs)
    print(f"Après dédup : {len(all_jobs)}")

    print("\nEnrichissement...")
    fetched = 0
    for i, job in enumerate(all_jobs):
        desc = job.get("description") or ""
        title = job.get("title", "")
        if not job.get("salary_raw"):
            job["salary_extracted"] = extract_salary(desc + " " + title)
        # Ne pas écraser les champs déjà posés par les sources 100 % remote
        if "telework_days" not in job:
            job["telework_days"] = extract_telework_days(title + " " + desc)
        if "in_france" not in job:
            job["in_france"] = is_in_france(job.get("location", ""), desc)
        if "contract_type" not in job:
            craw = (job.get("contract_raw") or "").lower()
            if craw == "contract":
                job["contract_type"] = "CDD"       # Adzuna : durée déterminée
            elif craw == "permanent" or check_cdi(title + " " + desc):
                job["contract_type"] = "CDI"
            else:
                job["contract_type"] = None

        # On va chercher le texte complet de l'annonce quand :
        #  - le télétravail est inconnu et la description est tronquée, OU
        #  - l'offre vient d'une alerte e-mail (description vide : on récupère
        #    ainsi télétravail/salaire ET on vérifie si l'annonce est toujours
        #    en ligne — les alertes pointent souvent des offres déjà pourvues).
        truncated = len(desc) >= 490 or desc.rstrip().endswith(("…", "..."))
        is_alert = "alerte" in (job.get("source") or "").lower()
        if (CONFIG.get("fetch_full_descriptions") and job.get("link")
                and ((job["telework_days"] is None and truncated) or is_alert)):
            full = fetch_full_text(job["link"])
            if full:
                if _EXPIRED_RE.search(full):
                    job["expired"] = True
                # Contrat (CDD / intérim / alternance) souvent au-delà des 500
                # premiers caractères tronqués par Adzuna : on le détecte ici sur
                # le texte complet (les blocs « offres similaires » sont déjà coupés).
                if _CONTRACT_EXCLUDE.search(full) or _ALT_STAGE_BODY.search(full):
                    job["contract_excluded"] = True
                if job.get("telework_days") is None:
                    job["telework_days"] = extract_telework_days(full)
                if not job.get("salary_raw") and not job.get("salary_extracted"):
                    job["salary_extracted"] = extract_salary(full)
                fetched += 1
                time.sleep(0.3)

        # Sites carrière (ATS Lever/Greenhouse…) : l'offre peut avoir été retirée
        # depuis la récupération via l'API. On vérifie que le lien est toujours
        # vivant (404/410 = supprimée) pour ne pas afficher d'offre morte.
        if ((job.get("source") or "").startswith("Site carrière") and job.get("link")
                and not job.get("expired") and _link_dead(job["link"])):
            job["expired"] = True
            time.sleep(0.2)

        # Trajet : inutile pour le 100 % télétravail
        loc = job.get("location", "")
        if job.get("telework_days") != 5 and loc and loc != "Île-de-France":
            job["commute_minutes"] = get_commute_time(loc)
            time.sleep(0.2)

        # Alertes de filtrage (Customer Success, contrat, séniorité, trajet...)
        job["flags"] = screen_offer(job)[2]

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(all_jobs)}...")
    print(f"  Annonces complètes récupérées : {fetched}")

    filtered, excl = [], 0
    for job in all_jobs:
        ok, _ = should_include(job)
        if ok:
            filtered.append(job)
        else:
            excl += 1

    print(f"\nFiltrage : {len(filtered)} retenues, {excl} exclues")

    # Retire les offres déjà écartées par Héloïse (dépôt privé), quelle que soit
    # la plateforme : une annonce rejetée ne doit jamais réapparaître.
    filtered, dropped = drop_archived(filtered)
    if dropped:
        print(f"Archives : {dropped} offre(s) déjà écartée(s) retirée(s)")

    filtered.sort(key=lambda j: compute_score(j)[0], reverse=True)

    export_json_local(filtered)
    write_to_sheets(filtered)
    send_email_recap(filtered)

    print("\n[8] Suivi des candidatures par email...")
    try:
        update_candidatures_tracking()
    except Exception as ex:
        print(f"  → Suivi candidatures : ERREUR {ex}")

    print("\n" + "=" * 60)
    print(f"✓ Terminé — {len(filtered)} offres")
    print("=" * 60)


if __name__ == "__main__":
    run()
