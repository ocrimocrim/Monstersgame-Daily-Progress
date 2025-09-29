#!/usr/bin/env python3
import os
import re
import sys
import time
from pathlib import Path
from html import unescape

import requests
from bs4 import BeautifulSoup

# -------------------------
# Konfiguration über ENV
# -------------------------
HIGHSCORE_URL = os.getenv("MG_HIGHSCORE_URL", "https://int3.monstersgame.moonid.net/index.php?ac=highscore&vid=0")
SESSIONID     = (os.getenv("MG_SESSIONID") or "").strip()
USERNAME      = (os.getenv("MG_USERNAME") or "").strip()
PASSWORD      = (os.getenv("MG_PASSWORD") or "").strip()
CONNECT_ID    = (os.getenv("MG_CONNECT_ID") or "").strip()     # optional
CSRF_TOKEN    = (os.getenv("MG_CSRFTOKEN") or "").strip()      # optional
DISCORD_WEBHOOK = (os.getenv("DISCORD_WEBHOOK") or "").strip()

PLAYERS_FILE = Path("players.txt")

# -------------------------
# Helpers
# -------------------------
def log(msg: str):
    print(msg, flush=True)

def is_logged_in_html(html: str) -> bool:
    # heuristisch: es muss eine Highscore-Tabelle geben
    return 'Your Rank' in html or 'tdh_highscore' in html or 'ac=highscore' in html

def normalize_name(name: str) -> str:
    s = unescape(name)
    s = re.sub(r"\s+", " ", s, flags=re.S).strip().lower()
    return s

def load_watchlist():
    names = []
    if PLAYERS_FILE.exists():
        for line in PLAYERS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                names.append(line)
    env_list = os.getenv("MG_WATCHLIST", "")
    if env_list.strip():
        names += [x.strip() for x in env_list.split(",") if x.strip()]
    # unique, normalisiert
    seen = set()
    out = []
    for n in names:
        nn = normalize_name(n)
        if nn not in seen:
            seen.add(nn)
            out.append(n)
    return out

def set_sso_cookie(session: requests.Session, sessionid: str):
    # SSO-Cookie muss für .moonid.net gesetzt sein
    if not sessionid:
        return
    session.cookies.set(
        name="sessionid",
        value=sessionid,
        domain=".moonid.net",
        path="/",
        secure=True
    )

def try_fetch_highscore(session: requests.Session) -> requests.Response | None:
    try:
        r = session.get(HIGHSCORE_URL, timeout=30, allow_redirects=True)
        if r.status_code == 200 and is_logged_in_html(r.text):
            return r
        return None
    except requests.RequestException:
        return None

def login_with_credentials(session: requests.Session) -> bool:
    """Robuster Login:
       1) Initial GET auf Login-Seite (setzt Cookies/Tokens)
       2) POST mit Username/Passwort (+ optionalen Feldern)
    """
    login_url = "https://int3.monstersgame.moonid.net/index.php?ac=login"

    try:
        # initial GET (holt csrftoken/Session-Cookies etc.)
        g = session.get(login_url, timeout=30)
    except requests.RequestException:
        return False

    payload = {
        # viele MG-Instanzen akzeptieren diese Feldnamen
        "username": USERNAME,
        "password": PASSWORD,
    }
    if CONNECT_ID:
        payload["connect_id"] = CONNECT_ID

    # Falls per ENV schon explizit ein csrftoken gegeben ist, mitsenden
    if CSRF_TOKEN:
        payload["csrftoken"] = CSRF_TOKEN

    # Manche Instanzen benutzen andere Felder – versuche zusätzlich gängige Aliase
    alt_payload = {
        "login": USERNAME,
        "pass": PASSWORD,
        "name": USERNAME,
        "pwd": PASSWORD,
    }

    # Wir probieren 2 Posts: erst Standard-Payload, dann Fallback mit Aliases
    for data in (payload, {**payload, **alt_payload}):
        try:
            p = session.post(login_url, data=data, timeout=30, allow_redirects=True)
        except requests.RequestException:
            continue
        # Wenn wir danach die Highscore sehen, gilt "eingeloggt"
        if p.status_code == 200:
            # manchmal landet man auf der Startseite – dann direkt Highscore testen
            r = try_fetch_highscore(session)
            if r is not None:
                return True
    return False

def parse_highscore(html: str):
    """Gibt eine Liste von (rang, name) zurück (normalisiert)."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) >= 3:
            # Spalte 1: Rang (Zahl oder #)
            rank_text = tds[0].get_text(strip=True)
            # Spalte 3: Name (Link)
            name_td = tds[2]
            name = name_td.get_text(" ", strip=True)
            if name and re.match(r"^\d+\.?$", rank_text):
                rows.append((rank_text, name))
    return rows

def post_discord(message: str):
    if not DISCORD_WEBHOOK:
        log(message)
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": message}, timeout=15)
    except requests.RequestException:
        log(message)

# -------------------------
# Main
# -------------------------
def main():
    if not PLAYERS_FILE.exists() and not os.getenv("MG_WATCHLIST", "").strip():
        log("Hinweis: Keine Watchlist gefunden (players.txt leer & MG_WATCHLIST nicht gesetzt).")
    watch_raw = load_watchlist()
    watch = {normalize_name(n): n for n in watch_raw}

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    })

    # 1) Cookie-Weg
    used_cookie = False
    if SESSIONID:
        set_sso_cookie(s, SESSIONID)
        r = try_fetch_highscore(s)
        if r is not None:
            used_cookie = True
        else:
            log("Session-Cookie funktioniert nicht (evtl. abgelaufen). Fallback: Login…")

    # 2) Login-Fallback
    if not used_cookie:
        if USERNAME and PASSWORD:
            ok = login_with_credentials(s)
            if not ok:
                log("Login fehlgeschlagen. Weder Cookie noch Credentials funktionieren.")
                sys.exit(0)
        else:
            log("Kein gültiger Cookie und keine Credentials gesetzt.")
            sys.exit(0)

    # 3) Jetzt Highscore abrufen (wir sollten drin sein)
    r = try_fetch_highscore(s)
    if r is None:
        log("Konnte Highscore trotz Login nicht laden.")
        sys.exit(0)

    rows = parse_highscore(r.text)
    if not rows:
        log("Konnte keine Rangliste parsen (HTML hat sich evtl. geändert).")
        sys.exit(0)

    # 4) Matchen
    hits = []
    for rank, name in rows:
        if normalize_name(name) in watch:
            hits.append((rank, name))

    # 5) Ausgabe
    if hits:
        date_str = time.strftime("%Y-%m-%d")
        lines = [f"Wolves, hört zu. Hier kommt die Beute.\nDatum {date_str}"]
        for rank, name in hits:
            lines.append(f"{rank} {name}")
        msg = "\n".join(lines)
    else:
        msg = f"Wolves, hört zu. Hier kommt die Beute.\nDatum {time.strftime('%Y-%m-%d')}\nKeine Treffer in der Watchlist. Prüfe Spielernamen oder Parser."

    post_discord(msg)

if __name__ == "__main__":
    main()
