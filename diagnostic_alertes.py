#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic PONCTUEL : inventorie les expéditeurs présents dans le dossier
Gmail « Alertes » de la boîte dédiée et indique, pour chacun, s'il est déjà
traité par le dashboard (présent dans _EMAIL_ALERT_SOURCES). Ne commit rien.
À supprimer après usage."""
import email
import imaplib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from email.header import decode_header, make_header

import job_scraper as js

LOOKBACK_DAYS = 60


def _decode(s):
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


def _find_alertes_folder(imap):
    """Renvoie le nom IMAP du dossier/label « Alertes » (sinon None)."""
    typ, data = imap.list()
    names = []
    if typ == "OK":
        for raw in data or []:
            line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            m = re.search(r'"([^"]+)"\s*$', line) or re.search(r'(\S+)\s*$', line)
            if m:
                names.append(m.group(1))
    for n in names:
        if re.search(r'(^|/)alertes?$', n, re.I):
            return n
    # repli : contient « alerte »
    for n in names:
        if re.search(r'alerte', n, re.I):
            return n
    print("  Dossiers disponibles :", names)
    return None


def main():
    address = js.CONFIG.get("gmail_address", "")
    password = js.CONFIG.get("gmail_app_password", "")
    if not address or not password:
        print("Gmail non configuré — abandon")
        return
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    imap.login(address, password)
    folder = _find_alertes_folder(imap)
    if not folder:
        print("Dossier « Alertes » introuvable.")
        imap.logout()
        return
    print(f"Dossier « Alertes » = {folder}")
    imap.select(f'"{folder}"', readonly=True)
    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    typ, data = imap.search(None, "SINCE", since)
    uids = (data[0].split() if (typ == "OK" and data and data[0]) else [])
    print(f"E-mails dans « Alertes » depuis {since} : {len(uids)}\n")

    # Expéditeurs configurés (traités par le dashboard).
    configured = []  # (nom_source, [senders])
    for cfg in js._EMAIL_ALERT_SOURCES:
        configured.append((cfg["name"], [s.lower() for s in cfg["senders"]]))

    senders = defaultdict(lambda: {"count": 0, "subjects": set(), "from": ""})
    for uid in uids:
        typ, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        frm = _decode(msg.get("From", ""))
        subj = _decode(msg.get("Subject", ""))
        m = re.search(r'[\w.+-]+@[\w.-]+', frm)
        addr = (m.group(0).lower() if m else frm.lower())
        dom = addr.split("@")[-1] if "@" in addr else addr
        key = dom
        senders[key]["count"] += 1
        senders[key]["from"] = frm
        if len(senders[key]["subjects"]) < 2:
            senders[key]["subjects"].add(subj[:70])

    def _handled(dom, frm):
        hay = f"{dom} {frm}".lower()
        for name, subs in configured:
            if any(s in hay for s in subs):
                return name
        return None

    print("=" * 78)
    print(f"{'DOMAINE EXPÉDITEUR':<34} {'N':>4}  ÉTAT")
    print("=" * 78)
    unhandled = []
    for dom in sorted(senders, key=lambda d: -senders[d]["count"]):
        info = senders[dom]
        name = _handled(dom, info["from"])
        state = f"✓ traité — {name}" if name else "✗ NON TRAITÉ"
        if not name:
            unhandled.append((dom, info))
        print(f"{dom:<34} {info['count']:>4}  {state}")
        for s in info["subjects"]:
            print(f"      · {s}")

    print("\n" + "=" * 78)
    if unhandled:
        print(f"⚠ {len(unhandled)} type(s) d'alerte NON traité(s) par le dashboard :")
        for dom, info in unhandled:
            print(f"   - {dom}  (from: {info['from']}) — {info['count']} e-mail(s)")
    else:
        print("✅ Tous les expéditeurs du dossier « Alertes » sont traités par le dashboard.")

    try:
        imap.close()
    except Exception:
        pass
    imap.logout()


if __name__ == "__main__":
    main()
