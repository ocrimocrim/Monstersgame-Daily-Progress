import os, json, random, re, sys
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------
# Basis-URLs und Pfade
# ---------------------------------------------------------------------
BASE_URL = "https://int3.monstersgame.moonid.net"
HIGHSCORE_URL = BASE_URL + "/index.php?ac=highscore&sac=spieler&highrasse=0&count=0&filter=gold_won&direction="
STATE_PATH = "data/state.json"
PLAYERS_FILE = "players.txt"

# moonID / Connect
MOONID_BASE = "https://moonid.net"
MG_CONNECT_ID = os.getenv("MG_CONNECT_ID") or "240"
MOONID_LOGIN_URL  = f"{MOONID_BASE}/account/login/?next=/api/account/connect/{MG_CONNECT_ID}/"
MOONID_CONNECT_URL = f"{MOONID_BASE}/api/account/connect/{MG_CONNECT_ID}/"

# Secrets / ENV
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
MG_USERNAME = os.environ.get("MG_USERNAME", "")
MG_PASSWORD = os.environ.get("MG_PASSWORD", "")
# Sessions/Cookies (alle optional – wenn gesetzt, werden sie genutzt)
MG_SESSIONID   = os.environ.get("MG_SESSIONID", "")     # sessionid auf int3
MG_PHPSESSID   = os.environ.get("MG_PHPSESSID", "")     # PHPSESSID auf int3
MG_COOKIE      = os.environ.get("MG_COOKIE", "")        # Alias: direkter PHPSESSID-Wert
MG_CSRFTOKEN   = os.environ.get("MG_CSRFTOKEN", "")     # falls moonid/seite es verlangt
MG_DEBUG       = os.environ.get("MG_DEBUG", "")         # "1" = ausführliches Log

SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}

MOTIVATION = [
    "Zeit, Beute zu zählen!",
    "Der Mond steht hoch – Zahlen auch.",
    "Wieder jemand reich geworden?",
    "Daily-Check: Wer hat kassiert?",
]

# ---------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------
def dbg(msg: str):
    if MG_DEBUG == "1":
        print(f"[DEBUG] {msg}")

def ensure_dirs():
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

def load_players_from_file():
    default_players = []
    if os.path.exists(PLAYERS_FILE):
        with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    # Fallback: MG_WATCHLIST aus ENV (Komma-getrennt)
    wl = os.environ.get("MG_WATCHLIST", "")
    if wl.strip():
        return [p.strip() for p in wl.split(",") if p.strip()]
    return default_players

def norm_name(s: str) -> str:
    # robust gegen Groß/Klein, doppelte/anhängende Leerzeichen
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"date": "", "players": {}}

def save_state(state):
    ensure_dirs()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def to_int(numstr: str) -> int:
    # entfernt Punkt/Komma-Tausendertrennungen und sonstige Nicht-Ziffern
    t = re.sub(r"[^\d]", "", numstr or "")
    return int(t) if t else 0

# ---------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------
def set_known_cookies(session: requests.Session):
    # Falls vorhanden, direkt Session-Cookies auf int3-Domain setzen
    # (wir setzen beide Varianten, wenn vorhanden)
    if MG_PHPSESSID:
        dbg("Setze Cookie: PHPSESSID (int3)")
        session.cookies.set("PHPSESSID", MG_PHPSESSID, domain="int3.monstersgame.moonid.net")
    if MG_SESSIONID:
        dbg("Setze Cookie: sessionid (int3)")
        session.cookies.set("sessionid", MG_SESSIONID, domain="int3.monstersgame.moonid.net")
    if MG_COOKIE and not MG_PHPSESSID:
        # Alias – einige Nutzer verwenden MG_COOKIE als PHPSESSID
        dbg("Setze Cookie: PHPSESSID aus MG_COOKIE (int3)")
        session.cookies.set("PHPSESSID", MG_COOKIE, domain="int3.monstersgame.moonid.net")

def looks_logged_in(html: str) -> bool:
    if not html:
        return False
    # sehr simple Heuristik: Highscore-Tabellenheader oder Logout-Link
    return ("bghighscore" in html) or ("Logout" in html) or ("logout" in html)

def login_via_moonid(session: requests.Session):
    # 1) Login-Seite holen
    dbg(f"GET {MOONID_LOGIN_URL}")
    r = session.get(MOONID_LOGIN_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # CSRF holen
    csrf = ""
    csrf_el = soup.find("input", attrs={"name": "csrfmiddlewaretoken"})
    if csrf_el and csrf_el.get("value"):
        csrf = csrf_el["value"]
    elif MG_CSRFTOKEN:
        csrf = MG_CSRFTOKEN  # falls per Secret vorgegeben
    if not csrf:
        dbg("Kein CSRF gefunden – versuchen wir es trotzdem.")

    # Login-Form-URL (action)
    form = soup.find("form")
    if not form or not form.get("action"):
        raise RuntimeError("Login-Form nicht gefunden.")
    login_url = form.get("action")
    if login_url.startswith("/"):
        login_url = MOONID_BASE + login_url

    # 2) Login absenden
    payload = {"username": MG_USERNAME, "password": MG_PASSWORD}
    if csrf:
        payload["csrfmiddlewaretoken"] = csrf

    headers = SESSION_HEADERS.copy()
    if csrf:
        headers["Referer"] = MOONID_LOGIN_URL

    dbg(f"POST {login_url}")
    r2 = session.post(login_url, data=payload, headers=headers, timeout=30, allow_redirects=True)
    r2.raise_for_status()

    # 3) Connect aufrufen (setzt Cookies für int3)
    dbg(f"GET {MOONID_CONNECT_URL}")
    r3 = session.get(MOONID_CONNECT_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r3.raise_for_status()

    # 4) Testaufruf Highscore
    dbg(f"GET {HIGHSCORE_URL} (Login-Check)")
    r4 = session.get(HIGHSCORE_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r4.raise_for_status()
    if not looks_logged_in(r4.text):
        raise RuntimeError("Login offenbar fehlgeschlagen (keine Highscore-Tabelle sichtbar).")

def get_highscore_html(session: requests.Session) -> str:
    dbg(f"GET {HIGHSCORE_URL}")
    r = session.get(HIGHSCORE_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text

# ---------------------------------------------------------------------
# Parsen der Highscore-Tabelle
# ---------------------------------------------------------------------
def parse_highscore(html: str):
    """
    Erwartet HTML (mit Tabelle). Nutzt den eingebauten 'html.parser',
    damit keine externen Parser (lxml) nötig sind.
    """
    soup = BeautifulSoup(html, "html.parser")

    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("Konnte keine Rangliste parsen (keine Tabelle gefunden).")

    # Größte Tabelle (meiste TRs) nehmen
    table = max(tables, key=lambda t: len(t.find_all("tr")))
    rows_out = []

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 10:
            continue  # Header oder unpassende Zeilen überspringen

        # Spalten lt. Beispiel:
        # 0 Rang, 1 Icon, 2 Name, 3 Lvl, 4 Loot, 5 Bites, 6 W, 7 L, 8 Anc.+, 9 Gold +
        # Name:
        name_el = tds[2].find("a")
        name = (name_el.get_text(strip=True) if name_el else tds[2].get_text(strip=True))

        # Metriken:
        lvl   = to_int(tds[3].get_text())
        wins  = to_int(tds[6].get_text())
        losses= to_int(tds[7].get_text())
        anc   = to_int(tds[8].get_text())
        gold  = to_int(tds[9].get_text())  # "Gold +" Spalte

        if not name:
            continue

        rows_out.append({
            "name": name,
            "level": lvl,
            "wins": wins,
            "losses": losses,
            "anc": anc,
            "gold": gold,
        })

    if not rows_out:
        raise RuntimeError("Konnte keine Rangliste parsen (Tabelle leer oder Struktur geändert).")

    return rows_out

# ---------------------------------------------------------------------
# Auswahl + Report
# ---------------------------------------------------------------------
def pick_players(rows, watchlist):
    wl_map = {norm_name(n): n.strip() for n in watchlist}
    out = {}
    for r in rows:
        key = norm_name(r["name"])
        if key in wl_map:
            out[wl_map[key]] = r  # Original-Schreibweise aus Watchlist beibehalten
    return out

def build_message(today, prev_players, today_players):
    report = []
    ranked = []

    for name, now in today_players.items():
        prev = prev_players.get(name, {})
        d_gold   = now["gold"]  - prev.get("gold", 0)
        d_wins   = now["wins"]  - prev.get("wins", 0)
        d_losses = now["losses"]- prev.get("losses", 0)
        d_anc    = now["anc"]   - prev.get("anc", 0)
        d_lvl    = now["level"] - prev.get("level", 0)

        extras = []
        if d_lvl:    extras.append(f"Lvl {('+' if d_lvl>=0 else '')}{d_lvl}")
        if d_anc:    extras.append(f"Anc {('+' if d_anc>=0 else '')}{d_anc}")
        line = f"{name} looted {d_gold} Gold, won {d_wins} fights, lost {d_losses} fights."
        if extras:
            line += " (" + ", ".join(extras) + ")"

        report.append((name, d_gold, line))
        ranked.append((name, d_gold))

    top_name, top_gold = (None, None)
    if ranked:
        top_name, top_gold = max(ranked, key=lambda x: x[1])

    intro = random.choice(MOTIVATION)
    date_str = today.strftime("%Y-%m-%d")

    lines = [f"{intro} — {date_str}", ""]
    if top_name is not None:
        lines.append(f"Top loot today: {top_name} (+{top_gold} Gold)")
        lines.append("")

    for _, _, l in sorted(report, key=lambda x: x[0].lower()):
        lines.append(l)

    return "\n".join(lines)

def send_discord(msg: str):
    if not DISCORD_WEBHOOK:
        print("Kein DISCORD_WEBHOOK gesetzt. Nachricht wird nur ausgegeben.\n")
        print(msg)
        return
    r = requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=30)
    r.raise_for_status()

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    watch = load_players_from_file()
    if not watch:
        print("Keine Spieler in players.txt oder MG_WATCHLIST gefunden – nichts zu tun.")
        sys.exit(0)

    state = load_state()
    prev_players = state.get("players", {})
    today = datetime.now(timezone.utc)

    with requests.Session() as s:
        s.headers.update(SESSION_HEADERS)

        # 1) Erst bekannte Cookies versuchen
        set_known_cookies(s)

        # 2) Highscore laden
        try:
            html = get_highscore_html(s)
            if not looks_logged_in(html):
                raise RuntimeError("Nicht eingeloggt, versuche Username/Passwort...")
        except Exception as e:
            dbg(f"Erster Versuch fehlgeschlagen: {e}")
            html = ""

        # 3) Falls nicht eingeloggt: MoonID-Login
        if not html or not looks_logged_in(html):
            if not MG_USERNAME or not MG_PASSWORD:
                raise RuntimeError("Login fehlgeschlagen. Weder gültige Cookies noch MG_USERNAME/MG_PASSWORD gesetzt.")
            login_via_moonid(s)
            html = get_highscore_html(s)

        # 4) Parsen
        rows = parse_highscore(html)

    # 5) Watchlist anwenden
    today_players = pick_players(rows, watch)

    # 6) Report
    msg = build_message(today, prev_players, today_players)
    send_discord(msg)

    # 7) State speichern
    new_state = {"date": today.isoformat(), "players": {n: today_players.get(n, {}) for n in today_players}}
    save_state(new_state)
    print("Protokoll gesendet und State gespeichert.")

if __name__ == "__main__":
    main()
