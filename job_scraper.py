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
import feedparser
import requests
import json
import time
import re
import smtplib
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


# ── Filtrage fin : règles issues de l'analyse des refus ─────────────────────────
# Deux niveaux : EXCLUSION (signaux non ambigus -> offre retirée) et
# ALERTE (signaux ambigus -> offre gardée avec un badge à revoir).

_TITLE_EXCLUDE = re.compile(
    r'\b(engineer|ing[ée]nieur|alternance|alternant[e]?|apprenti[e]?|'
    r'stage|stagiaire|internship|\bintern\b)\b', re.I)

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

_FOREIGN_RESIDENCE = re.compile(
    r'(?:based in|reside in|residents? of|located in|work from)\s+(?:the\s+)?'
    r'(united kingdom|\buk\b|canada|germany|deutschland|mexico|south africa|'
    r'spain|espagne|portugal|belgium|belgique|switzerland|suisse|india|inde)', re.I)

_CONTRACT_TERMS = re.compile(
    r'independent contractor|contractor agreement|commission[- ]based|'
    r'rev(?:enue)?[- ]share|uncapped earnings|per hour|/\s*hr\b|\$\s*\d+\s*/\s*h|'
    r'south african employment|\b1099\b', re.I)

_STAFFING_COMPANIES = ["kicklox", "synopsia"]
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


def screen_offer(job):
    """Renvoie (exclure: bool, motif: str|None, alertes: list[str])."""
    title = job.get("title") or ""
    text = f"{title} {job.get('description') or ''} {job.get('company') or ''}"
    tl, cl = title.lower(), (job.get("company") or "").lower()
    has_mkt = bool(_MARKETING_SIGNALS.search(text))
    flags = []

    # ---- EXCLUSIONS (signaux non ambigus) ----
    if _TITLE_EXCLUDE.search(title):
        return True, "Titre exclu (engineer / alternance / stage)", flags
    if any(c in cl for c in _MEDICAL_COMPANIES) or _MEDICAL_TERMS.search(text):
        return True, "CRM médical (dispositifs cardiaques)", flags
    if _RETAIL_TERMS.search(text):
        return True, "CRM = caisse / magasin", flags
    if _AUTO_TERMS.search(text):
        return True, "Secteur automobile", flags
    if _US_RESIDENCE.search(text):
        return True, "Résidence / citoyenneté US requise", flags

    # ---- ALERTES (signaux ambigus, on garde et on signale) ----
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
        flags.append("Via ESN / staffing")
    if _SPECIFIC_ESP.search(text) and _PROG_TERMS.search(text) and _NICHE_SECTOR.search(text):
        flags.append("Écart technique large")
    if any(int(y) > 7 for y in _SENIOR_YEARS.findall(text)):
        flags.append("Séniorité élevée (>7 ans ?)")
    if _TEAM_MGMT.search(text):
        flags.append("Management d'équipe ?")
    if _ENTRY_LEVEL.search(text):
        flags.append("Poste junior / débutant ?")

    tw = job.get("telework_days")
    if tw is None and not _REMOTE_MENTION.search(text):
        flags.append("Télétravail non mentionné")
    elif isinstance(tw, int) and 0 <= tw <= 1:
        flags.append(f"Télétravail faible ({tw} j)")

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


def fetch_arbeitnow_jobs():
    """Arbeitnow : API gratuite (Europe / remote), sans clé."""
    print("  → Arbeitnow API...")
    jobs = []
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api",
                         headers={"User-Agent": "JobScraper/1.0"}, timeout=15)
        r.raise_for_status()
        for o in r.json().get("data", []):
            loc = o.get("location", "") or ""
            remote = bool(o.get("remote"))
            jobs.append({
                "source": "Arbeitnow",
                "title": o.get("title", ""),
                "link": o.get("url", ""),
                "company": o.get("company_name", ""),
                "location": ("Remote — " + loc) if remote else loc,
                "description": o.get("description", ""),
                "published": o.get("created_at", ""),
                "telework_days": 5 if remote else None,
                "in_france": remote_scope_in_france(loc + (" remote" if remote else "")),
            })
    except Exception as ex:
        print(f"     ERREUR Arbeitnow : {ex}")
    jobs = [j for j in jobs if is_relevant(j)]
    unique = _dedup(jobs)
    print(f"     {len(unique)} offres pertinentes")
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
        return re.sub(r'\s+', ' ', text)[:20000]
    except Exception:
        return ""


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

def _dedup(jobs):
    seen, unique = set(), []
    for j in jobs:
        k = (j["title"].lower()[:40], j.get("company", "").lower()[:20])
        if k not in seen:
            seen.add(k)
            unique.append(j)
    return unique


def export_json_local(jobs, path="jobs_output.json"):
    out = [{**j, "score": compute_score(j)[0], "score_reasons": compute_score(j)[1]} for j in jobs]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"✓ JSON : {path} ({len(out)} offres)")


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

    print("\n[6] The Muse + Arbeitnow...")
    all_jobs.extend(fetch_themuse_jobs())
    all_jobs.extend(fetch_arbeitnow_jobs())

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
            job["contract_type"] = "CDI" if check_cdi(title + " " + desc) else None

        # Télétravail introuvable + description probablement tronquée
        # -> on va chercher le texte complet de l'annonce.
        truncated = len(desc) >= 490 or desc.rstrip().endswith(("…", "..."))
        if (CONFIG.get("fetch_full_descriptions") and job["telework_days"] is None
                and truncated and job.get("link")):
            full = fetch_full_text(job["link"])
            if full:
                job["telework_days"] = extract_telework_days(full)
                if not job.get("salary_raw") and not job.get("salary_extracted"):
                    job["salary_extracted"] = extract_salary(full)
                fetched += 1
                time.sleep(0.3)

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
    filtered.sort(key=lambda j: compute_score(j)[0], reverse=True)

    export_json_local(filtered)
    write_to_sheets(filtered)
    send_email_recap(filtered)

    print("\n" + "=" * 60)
    print(f"✓ Terminé — {len(filtered)} offres")
    print("=" * 60)


if __name__ == "__main__":
    run()
