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
from datetime import datetime
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
    for kw in ["campaign manager CRM", "chargé CRM", "chef de projet CRM", "marketing automation"]:
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
    for kw in ["CRM manager", "campaign manager CRM", "chef de projet CRM", "marketing automation"]:
        try:
            r = requests.get(
                "https://api.adzuna.com/v1/api/jobs/fr/search/1",
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
            for o in r.json().get("results", []):
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
            time.sleep(0.5)
        except Exception as ex:
            print(f"     ERREUR '{kw}': {ex}")

    unique = _dedup(all_jobs)
    print(f"     {len(unique)} offres uniques")
    return unique


# ── Enrichissement ─────────────────────────────────────────────────────────────

def get_commute_time(destination):
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
    commute = job.get("commute_minutes")
    if commute and commute > CONFIG["max_commute_minutes"]:
        return False, f"Trajet trop long ({commute} min)"

    tw = job.get("telework_days")
    if tw is not None and tw < CONFIG["min_telework_days"]:
        return False, f"Télétravail insuffisant ({tw}j)"

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

    print("\n[3/4] France Travail (API)...")
    all_jobs.extend(fetch_francetravail_jobs())

    print("\n[4/4] Adzuna (API)...")
    all_jobs.extend(fetch_adzuna_jobs())

    print(f"\nTotal brut : {len(all_jobs)}")
    all_jobs = _dedup(all_jobs)
    print(f"Après dédup : {len(all_jobs)}")

    print("\nEnrichissement...")
    for i, job in enumerate(all_jobs):
        desc = job.get("description") or ""
        title = job.get("title", "")
        if not job.get("salary_raw"):
            job["salary_extracted"] = extract_salary(desc + " " + title)
        job["telework_days"] = extract_telework_days(title + " " + desc)
        job["in_france"] = is_in_france(job.get("location", ""), desc)
        loc = job.get("location", "")
        if loc and loc != "Île-de-France":
            job["commute_minutes"] = get_commute_time(loc)
            time.sleep(0.2)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(all_jobs)}...")

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
