#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic PONCTUEL : inspecte la structure réelle des e-mails d'alerte
Talent.com dans la boîte Gmail dédiée, pour écrire un parseur fiable.
Ne commit rien, n'écrit aucune offre. À supprimer après usage."""
import email
import imaplib
from datetime import datetime, timedelta

import job_scraper as js

SENDERS = ["talent.com", "alerts.talent.com", "no-reply@alerts.talent.com"]


def main():
    address = js.CONFIG.get("gmail_address", "")
    password = js.CONFIG.get("gmail_app_password", "")
    if not address or not password:
        print("Gmail non configuré — abandon")
        return
    since = (datetime.now() - timedelta(days=14)).strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    imap.login(address, password)
    folder = js._gmail_all_mail_folder(imap)
    imap.select(f'"{folder}"', readonly=True)
    print(f"Dossier lu : {folder}   depuis : {since}")
    uids = set()
    for sender in SENDERS:
        try:
            typ, data = imap.search(None, "SINCE", since, "FROM", sender)
            if typ == "OK" and data and data[0]:
                uids.update(data[0].split())
        except Exception as ex:
            print(f"  recherche {sender}: {ex}")
    print(f"UIDs Talent.com trouvés : {len(uids)}")
    for uid in list(uids)[:3]:  # au plus 3 e-mails
        typ, msg_data = imap.fetch(uid, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        print("\n" + "=" * 70)
        print("SUBJECT :", msg.get("Subject", ""))
        print("FROM    :", msg.get("From", ""))
        html, text = js._email_body_html(msg)
        print(f"len(html)={len(html)}  len(text)={len(text)}")
        # 1) Toutes les ancres href + texte (pour repérer le motif d'URL d'offre).
        print("\n--- ANCRES (href | texte) ---")
        n = 0
        for m in js._ANCHOR_RE.finditer(html):
            href = m.group("href")
            txt = js._HTML_TAG_RE.sub(" ", m.group("text"))
            import html as _h
            txt = _h.unescape(txt)
            import re as _re
            txt = _re.sub(r"\s+", " ", txt).strip()
            if not href.startswith("http"):
                continue
            n += 1
            if n > 60:
                break
            print(f"[{n:02d}] {href[:120]}  ||  {txt[:80]}")
        # 2) Lignes de texte du corps (structure titre/lieu/société/badges).
        print("\n--- LIGNES DU CORPS (80 premières) ---")
        for i, ln in enumerate(js._html_lines(html)[:80]):
            print(f"{i:03d}| {ln[:110]}")
    try:
        imap.close()
    except Exception:
        pass
    imap.logout()


if __name__ == "__main__":
    main()
