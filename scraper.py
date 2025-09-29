import os
import re
import sys
from typing import List, Tuple, Dict

import requests
from bs4 import BeautifulSoup

# -------------------- ENV --------------------
HIGHSCORE_URL = os.getenv("MG_HIGHSCORE_URL", "").strip()
SESSIONID     = os.getenv("MG_SESSIONID", "").strip()
CSRF_TOKEN    = os.getenv("MG_CSRFTOKEN", "").strip()      # optional
CONNECT_ID    = os.getenv("MG_CONNECT_ID", "").strip()     # optional
USERNAME      = os.getenv("MG_USERNAME", "").strip()       # optional Fallback
PASSWORD      = os.getenv("MG_PASSWORD", "").strip()       # optional Fallback
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

WATCHLIST_FILE = "players.txt"

# -------------------- Utils --------------------
def norm_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.strip("[](){}.,;:!?'\"")
    return s

def load_watchlist(path: str) -> List[str]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]

def send_discord(text: str):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": text}, timeout=20)
    except Exception:
        pass

# -------------------- Session/Cookies --------------------
def prepare_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    })
    if SESSIONID:
        # auf beide Domains setzen – einige Seiten prüfen streng
        s.cookies.set("sessionid", SESSIONID, domain=".moonid.net", path="/")
        s.cookies.set("sessionid", SESSIONID, domain="int3.monstersgame.moonid.net", path="/")
    if CSRF_TOKEN:
        s.cookies.set("csrftoken", CSRF_TOKEN, domain=".moonid.net", path="/")
        s.cookies.set("csrftoken", CSRF_TOKEN, domain="int3.monstersgame.moonid.net", path="/")
    return s

def fetch(url: str, s: requests.Session) -> str:
    r = s.get(url, timeout=40, allow_redirects=True)
    r.raise_for_status()
    return r.text

# -------------------- Parser --------------------
def largest_table(soup: BeautifulSoup):
    tables = soup.find_all("table")
    if not tables:
        return None
    return max(tables, key=lambda t: len(t.find_all("tr")))

def parse_highscore_names(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    table = largest_table(soup)
    if not table:
        return []
    names = []
    for tr in table.find_all("tr"):
        a = tr.find("a", href=lambda h: h and "showuser" in h)
        if a:
            txt = a.get_text(strip=True)
            if txt:
                names.append(txt)
    # uniq
    seen = set(); out=[]
    for n in names:
        if n not in seen:
            seen.add(n); out.append(n)
    return out

def match_watchlist(found: List[str], watch: List[str]) -> Tuple[List[str], List[str]]:
    found_norm = {norm_name(n): n for n in found}
    hits, misses = [], []
    for w in watch:
        wn = norm_name(w)
        if wn in found_norm:
            hits.append(found_norm[wn])
        else:
            misses.append(w)
    return hits, misses

# -------------------- Login Fallback --------------------
def form_to_dict(form) -> Dict[str,str]:
    data = {}
    for inp in form.find_all(["input","select","textarea"]):
        name = inp.get("name")
        if not name: 
            continue
        val = inp.get("value", "")
        data[name] = val
    return data

def try_password_login(s: requests.Session) -> bool:
    if not (HIGHSCORE_URL and USERNAME and PASSWORD):
        return False
    base = HIGHSCORE_URL.split("/index.php")[0]
    login_url = base + "/index.php?ac=login"
    try:
        # 1) Login-Seite holen (Cookies, versteckte Felder)
        resp = s.get(login_url, timeout=40, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form")
        data = form_to_dict(form) if form else {}

        # 2) Feldnamen tolerant setzen
        # mögliche Schlüsselnamen für Benutzer/Passwort
        user_keys = ["username", "login", "user", "nick", "mail", "email"]
        pass_keys = ["password", "pass", "pw"]

        def put_key(keys, value):
            for k in keys:
                if k in data: 
                    data[k] = value; return
            # wenn keins existiert, nimm das erste
            data[keys[0]] = value

        put_key(user_keys, USERNAME)
        put_key(pass_keys, PASSWORD)

        # optionale Tokens hinzufügen
        if CONNECT_ID and "connect_id" not in data:
            data["connect_id"] = CONNECT_ID
        if CSRF_TOKEN:
            # gängige Namen
            for k in ("csrftoken", "csrf_token", "csrfmiddlewaretoken"):
                if k not in data:
                    data[k] = CSRF_TOKEN

        # manche Formulare benötigen expliziten Submit-Name
        if "login" not in data:
            data["login"] = "Login"

        headers = {"Referer": login_url}
        s.post(login_url, data=data, headers=headers, timeout=40, allow_redirects=True)

        # 3) Testabruf
        test_html = s.get(HIGHSCORE_URL, timeout=40, allow_redirects=True).text
        # wenn wir Tabelle parsen können, gilt Login als erfolgreich
        return len(parse_highscore_names(test_html)) > 0
    except Exception:
        return False

# -------------------- Main --------------------
def main():
    if not HIGHSCORE_URL:
        print("Fehler: MG_HIGHSCORE_URL ist nicht gesetzt.")
        sys.exit(1)

    s = prepare_session()

    # Versuch 1: einfach laden & parsen
    try:
        html = fetch(HIGHSCORE_URL, s)
    except Exception as e:
        print(f"Netzwerkfehler: {e}")
        sys.exit(1)

    names = parse_highscore_names(html)

    # Falls leer: Login-Fallback
    if not names and (USERNAME and PASSWORD):
        if try_password_login(s):
            html = fetch(HIGHSCORE_URL, s)
            names = parse_highscore_names(html)
        else:
            # Kein harter Abbruch mehr – wir melden sauber
            print("Login fehlgeschlagen. Cookie + Credentials halfen nicht.")
            return

    if not names:
        print("Konnte keine Rangliste parsen (keine Tabelle/Links gefunden).")
        return

    watch = load_watchlist(WATCHLIST_FILE)
    if not watch:
        print("Hinweis: players.txt leer oder fehlt. Gefundene Namen (Top-Auszug):")
        print(", ".join(names[:20]))
        return

    hits, misses = match_watchlist(names, watch)
    msg_lines = []
    if hits:
        msg_lines.append("🎯 Gefunden: " + ", ".join(hits))
    if misses:
        msg_lines.append("❌ Nicht gefunden: " + ", ".join(misses))
    out = "\n".join(msg_lines) if msg_lines else "Keine Watchlist-Treffer."
    print(out)
    send_discord(out)

if __name__ == "__main__":
    main()
