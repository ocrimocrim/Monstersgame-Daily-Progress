import os, json, random, re, sys
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------
# Basis-URLs & Dateien
# ------------------------------------------------------------
BASE_URL = "https://int3.monstersgame.moonid.net"
HIGHSCORE_URL = BASE_URL + "/index.php?ac=highscore&sac=spieler&highrasse=0&count=0&filter=gold_won&direction="
STATE_PATH = "data/state.json"
PLAYERS_FILE = "players.txt"

# moonID Connect
MOONID_BASE = "https://moonid.net"
MG_CONNECT_ID = os.getenv("MG_CONNECT_ID") or "240"  # robustes Fallback
MOONID_LOGIN_URL   = f"{MOONID_BASE}/account/login/?next=/api/account/connect/{MG_CONNECT_ID}/"
MOONID_CONNECT_URL = f"{MOONID_BASE}/api/account/connect/{MG_CONNECT_ID}/"

# ------------------------------------------------------------
# Secrets / ENV
# ------------------------------------------------------------
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")

# Login-Credentials (Fallback, falls keine gültigen Cookies)
MG_USERNAME = os.environ.get("MG_USERNAME", "")
MG_PASSWORD = os.environ.get("MG_PASSWORD", "")

# Cookies aus deinen DevTools / GitHub-Secrets
# - MG_PHPSESSID  => Cookie-Wert von "PHPSESSID" (Domain: int3.monstersgame.moonid.net)
# - MG_SESSIONID  => Cookie-Wert von "sessionid"  (Domain: moonid.net)
MG_PHPSESSID = os.environ.get("MG_PHPSESSID", "").strip()
MG_SESSIONID = os.environ.get("MG_SESSIONID", "").strip()

# Optional: CSRF (aus moonid.net Cookies), nur falls wirklich nötig
MG_CSRFTOKEN = os.environ.get("MG_CSRFTOKEN", "").strip()

# Debug-Ausgaben einschalten mit Secret MG_DEBUG=1
DEBUG = os.environ.get("MG_DEBUG", "") == "1"

SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}

MOTIVATION = [
    "Stay sharp, wolves!",
    "Another day, another loot.",
    "Keep hunting!",
    "Gold never sleeps.",
]

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def log(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")

def ensure_dirs():
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

def read_players():
    # Nur die Datei players.txt; wenn nicht vorhanden, leere Liste
    if os.path.exists(PLAYERS_FILE):
        with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"date": "", "players": {}}

def save_state(state):
    ensure_dirs()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def parse_int(text):
    # "1,399,007" -> 1399007
    return int(re.sub(r"[^\d]", "", text)) if re.search(r"\d", text) else 0

def normalized_name(s):
    # Normalisiert wie besprochen (Leerzeichen, Klammern, Sonderzeichen tolerant)
    return re.sub(r"\s+", " ", s).strip().lower()

def login_ok(html: str) -> bool:
    # Simpler Check: es gibt den Highscore-Header oder Logout-Links
    return ("tdh_highscore" in html) or ("Logout" in html) or ("logout" in html)

# ------------------------------------------------------------
# Login-Flow (Cookie-Fallback -> moonID Login)
# ------------------------------------------------------------
def prime_cookies(session: requests.Session):
    """
    Setzt vorhandene Cookies aus Secrets:
      - PHPSESSID auf int3.monstersgame.moonid.net
      - sessionid / csrftoken auf moonid.net (falls vorhanden)
    """
    if MG_PHPSESSID:
        session.cookies.set("PHPSESSID", MG_PHPSESSID, domain="int3.monstersgame.moonid.net")
        log("Set cookie: PHPSESSID (int3)")

    if MG_SESSIONID:
        session.cookies.set("sessionid", MG_SESSIONID, domain="moonid.net")
        log("Set cookie: sessionid (moonid.net)")

    if MG_CSRFTOKEN:
        session.cookies.set("csrftoken", MG_CSRFTOKEN, domain="moonid.net")
        log("Set cookie: csrftoken (moonid.net)")

def try_highscore(session: requests.Session) -> str | None:
    r = session.get(HIGHSCORE_URL, headers={"User-Agent": SESSION_HEADERS["User-Agent"]}, timeout=30, allow_redirects=True)
    log(f"GET highscore -> {r.status_code} (final URL: {r.url})")
    if r.status_code == 200 and login_ok(r.text):
        return r.text
    return None

def login_via_moonid(session: requests.Session) -> str | None:
    """
    Führt den vollständigen moonID-Login + Connect aus und gibt Highscore-HTML zurück,
    wenn alles klappt. Sonst None.
    """
    if not MG_USERNAME or not MG_PASSWORD:
        log("Kein MG_USERNAME/MG_PASSWORD gesetzt – kann keinen Login durchführen.")
        return None

    # 1) Login-Seite aufrufen (holt auch CSRF)
    r = session.get(MOONID_LOGIN_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    log(f"GET login page -> {r.status_code} (final URL: {r.url})")
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    form = soup.find("form")
    if not form:
        log("Login-Formular nicht gefunden.")
        return None

    payload = {}
    for inp in form.find_all("input"):
        name = (inp.get("name") or "").strip()
        value = inp.get("value") or ""
        if name:
            payload[name] = value

    # CSRF-Feld heuristisch finden
    if "csrfmiddlewaretoken" not in payload:
        # manche Installationen nutzen andere Namen
        for k in list(payload.keys()):
            if k.lower().startswith("csrf"):
                payload["csrfmiddlewaretoken"] = payload[k]
                break

    # Verbindlich Benutzer & Passwort setzen
    payload["username"] = MG_USERNAME
    payload["password"] = MG_PASSWORD

    # 2) POST Login
    action = form.get("action") or MOONID_LOGIN_URL
    login_url = action if action.startswith("http") else (MOONID_BASE.rstrip("/") + "/" + action.lstrip("/"))
    r2 = session.post(login_url, data=payload, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    log(f"POST login -> {r2.status_code} (final URL: {r2.url})")
    r2.raise_for_status()

    # 3) Connect aufrufen, damit Cookies auf int3 landen
    r3 = session.get(MOONID_CONNECT_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    log(f"GET connect -> {r3.status_code} (final URL: {r3.url})")
    r3.raise_for_status()

    # 4) Test: Highscore holen
    return try_highscore(session)

# ------------------------------------------------------------
# Parser für die Highscore-Tabelle
# ------------------------------------------------------------
def parse_highscore(html: str):
    soup = BeautifulSoup(html, "lxml")
    # größte Tabelle nehmen (robust)
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("Konnte keine Tabelle finden (HTML-Struktur geändert?).")
    t = max(tables, key=lambda tb: len(tb.find_all("tr")))

    rows = []
    for tr in t.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        # Spalten: #, Race, Name, Lvl, Loot, Bites, W, L, Anc.+, Gold+
        name_a = tds[2].find("a")
        name = (name_a.text if name_a else tds[2].get_text()).strip()
        if not name:
            continue

        row = {
            "name": name,
            "level": parse_int(tds[3].get_text()),
            "loot": parse_int(tds[4].get_text()),
            "bites": parse_int(tds[5].get_text()),
            "wins": parse_int(tds[6].get_text()),
            "losses": parse_int(tds[7].get_text()),
            "anc": parse_int(tds[8].get_text()),
            "gold": parse_int(tds[9].get_text()),
        }
        rows.append(row)

    if not rows:
        raise RuntimeError("Konnte keine Rangliste parsen (keine Zeilen gefunden).")
    return rows

# ------------------------------------------------------------
# Watchlist-Filter & Bericht
# ------------------------------------------------------------
def pick_players(rows, watchlist):
    wl = {normalized_name(n): n.strip() for n in watchlist}
    out = {}
    for r in rows:
        key = normalized_name(r["name"])
        if key in wl:
            out[wl[key]] = r
    return out

def build_message(today, prev_players, today_players):
    report, ranked = [], []
    for name, now in today_players.items():
        prev = prev_players.get(name, {})
        d_gold   = now["gold"]   - prev.get("gold", 0)
        d_wins   = now["wins"]   - prev.get("wins", 0)
        d_losses = now["losses"] - prev.get("losses", 0)
        d_anc    = now["anc"]    - prev.get("anc", 0)
        d_lvl    = now["level"]  - prev.get("level", 0)

        line = f"{name} looted {d_gold} Gold, won {d_wins} fights, lost {d_losses} fights."
        extras = []
        if d_lvl:  extras.append(f"lvl +{d_lvl}")
        if d_anc:  extras.append(f"anc +{d_anc}")
        if extras:
            line += " (" + ", ".join(extras) + ")"

        report.append((name, d_gold, line))
        ranked.append((name, d_gold))

    top_name, top_gold = (None, None)
    if ranked:
        top_name, top_gold = max(ranked, key=lambda x: x[1])

    intro = random.choice(MOTIVATION)
    date_str = today.strftime("%Y-%m-%d")

    lines = [f"**{intro}**  —  *{date_str}*"]
    if top_name is not None:
        lines.append(f"Top looter today: **{top_name}** (+{top_gold} Gold)")

    for _, _, ln in sorted(report, key=lambda x: x[0].lower()):
        lines.append("• " + ln)

    return "\n".join(lines)

def send_discord(msg: str):
    if not DISCORD_WEBHOOK:
        print("Kein DISCORD_WEBHOOK gesetzt. Nachricht wird nur ausgegeben:")
        print(msg)
        return
    r = requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=30)
    r.raise_for_status()

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    watchlist = read_players()
    if not watchlist:
        print("players.txt ist leer oder fehlt – nichts zu tun.")
        return

    prev_state = load_state()
    prev_players = prev_state.get("players", {})
    today = datetime.now(timezone.utc)

    with requests.Session() as s:
        s.headers.update(SESSION_HEADERS)

        # 1) vorhandene Cookies setzen
        prime_cookies(s)

        # 2) versuchen, direkt Highscore zu laden
        html = try_highscore(s)

        # 3) falls das nicht klappt: moonID-Login versuchen
        if html is None:
            log("Direkter Zugriff mit Cookies fehlgeschlagen – versuche moonID-Login…")
            html = login_via_moonid(s)

        if html is None:
            print("Login fehlgeschlagen. Cookie + Credentials halfen nicht.")
            return

        # 4) Parsen & Filtern
        rows = parse_highscore(html)
        today_players = pick_players(rows, watchlist)

    # 5) Nachricht & State
    msg = build_message(today, prev_players, today_players)
    send_discord(msg)

    new_state = {"date": today.isoformat(), "players": {n: today_players[n] for n in today_players}}
    save_state(new_state)
    print("Protokoll gesendet und State gespeichert.")

if __name__ == "__main__":
    main()
