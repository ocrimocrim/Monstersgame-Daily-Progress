import os
import re
import sys
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------
# ENV / Secrets
# ---------------------------------------------------
HIGHSCORE_URL = os.getenv("MG_HIGHSCORE_URL", "").strip()
SESSIONID      = os.getenv("MG_SESSIONID", "").strip()
CSRF_TOKEN     = os.getenv("MG_CSRFTOKEN", "").strip()   # optional
USERNAME       = os.getenv("MG_USERNAME", "").strip()    # Fallback-Login
PASSWORD       = os.getenv("MG_PASSWORD", "").strip()    # Fallback-Login
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

WATCHLIST_FILE = "players.txt"   # eine Zeile pro Name


# ---------------------------------------------------
# Helpers
# ---------------------------------------------------
def norm_name(s: str) -> str:
    """Sanfte Normalisierung: Kleinschreibung, Whitespace komprimieren,
    unkritische Satzzeichen am Rand weg."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.strip("[](){}.,;:!?'\"")
    return s

def load_watchlist(path: str) -> List[str]:
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t:
                out.append(t)
    return out

def send_discord(text: str):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": text}, timeout=20)
    except Exception:
        pass


# ---------------------------------------------------
# Login / Fetch
# ---------------------------------------------------
def prepare_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    })
    # sessionid-Cookie (Domain: .moonid.net reicht für int3.*)
    if SESSIONID:
        s.cookies.set("sessionid", SESSIONID, domain=".moonid.net", path="/")
    if CSRF_TOKEN:
        s.cookies.set("csrftoken", CSRF_TOKEN, domain=".moonid.net", path="/")
    return s

def fetch(url: str, s: requests.Session) -> requests.Response:
    r = s.get(url, timeout=40, allow_redirects=True)
    return r

def looks_logged_out(html: str) -> bool:
    hay = html.lower()
    if "ac=login" in hay or "name:" in hay and "password" in hay:
        return True
    # Wenn keine showuser-Links auftauchen, ist es oft Login/Fehlerseite
    if "showuser" not in hay:
        return True
    return False

def try_password_login(s: requests.Session) -> bool:
    """Sehr generisches Login – falls der Cookie tot ist.
    Die tatsächlichen Felder variieren je nach Spielversion.
    Wir probieren Standard-Parameter und hoffen auf 200 + showuser in der Antwort."""
    if not (USERNAME and PASSWORD and HIGHSCORE_URL):
        return False
    base = HIGHSCORE_URL.split("/index.php")[0]
    login_url = base + "/index.php?ac=login"
    data = {
        "username": USERNAME,
        "password": PASSWORD,
        "login": "Login"
    }
    try:
        s.post(login_url, data=data, timeout=40, allow_redirects=True)
        test = s.get(HIGHSCORE_URL, timeout=40, allow_redirects=True)
        return not looks_logged_out(test.text)
    except Exception:
        return False


# ---------------------------------------------------
# Parser für die Highscore-Tabelle
# ---------------------------------------------------
def parse_highscore_names(html: str) -> List[str]:
    """Nimmt die größte Tabelle und extrahiert die Namen aus <a href*='showuser'>."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return []

    # größte Tabelle = die mit meisten <tr>
    def tr_count(tbl): return len(tbl.find_all("tr"))
    table = max(tables, key=tr_count)

    names = []
    for tr in table.find_all("tr"):
        a = tr.find("a", href=lambda h: h and "showuser" in h)
        if not a:
            continue
        name = a.get_text(strip=True)
        if name:
            names.append(name)
    # Duplikate raus
    seen = set()
    uniq = []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


# ---------------------------------------------------
# Matchen gegen Watchlist
# ---------------------------------------------------
def match_watchlist(found: List[str], watch: List[str]) -> Tuple[List[str], List[str]]:
    found_norm = {norm_name(n): n for n in found}
    hits = []
    misses = []
    for w in watch:
        wn = norm_name(w)
        if wn in found_norm:
            hits.append(found_norm[wn])  # Originalname aus Highscore
        else:
            misses.append(w)
    return hits, misses


# ---------------------------------------------------
# Main
# ---------------------------------------------------
def main():
    if not HIGHSCORE_URL:
        print("Fehler: MG_HIGHSCORE_URL ist nicht gesetzt.")
        sys.exit(1)

    session = prepare_session()

    # 1) mit Cookie probieren
    try:
        resp = fetch(HIGHSCORE_URL, session)
        html = resp.text
    except Exception as e:
        print(f"Netzwerkfehler: {e}")
        sys.exit(1)

    # 2) Falls ausgeloggt: Passwort-Login probieren
    if looks_logged_out(html):
        if try_password_login(session):
            html = session.get(HIGHSCORE_URL, timeout=40).text
        else:
            print("Login fehlgeschlagen. Weder Cookie noch Credentials funktionieren.")
            sys.exit(0)

    # 3) Namen parsen (neuer robuster Parser)
    names = parse_highscore_names(html)
    if not names:
        print("Konnte keine Rangliste parsen (HTML/Tabelle nicht gefunden).")
        sys.exit(0)

    watch = load_watchlist(WATCHLIST_FILE)
    if not watch:
        print("Hinweis: players.txt leer oder fehlt. Gefundene Namen (Top-Auszug):")
        print(", ".join(names[:20]))
        sys.exit(0)

    hits, misses = match_watchlist(names, watch)

    # Ausgabe / Discord
    msg_lines = []
    if hits:
        msg_lines.append("🎯 Gefunden (auf der Rangliste): " + ", ".join(hits))
    if misses:
        msg_lines.append("❌ Nicht gefunden: " + ", ".join(misses))
    out_msg = "\n".join(msg_lines) if msg_lines else "Keine Watchlist-Treffer."

    print(out_msg)
    send_discord(out_msg)


if __name__ == "__main__":
    main()
