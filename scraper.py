import os, json, random, re, sys
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

# Basis
BASE_URL = "https://int3.monstersgame.moonid.net"
HIGHSCORE_URL = BASE_URL + "/index.php?ac=highscore&sac=spieler&highrasse=0&count=0&filter=gold_won&direction="
STATE_PATH = "data/state.json"
PLAYERS_FILE = "players.txt"

# moonID
MOONID_BASE = "https://moonid.net"
MOONID_CONNECT_ID = os.environ.get("MG_CONNECT_ID", "240")  # bei Bedarf im Repo als Secret setzen
MOONID_LOGIN_URL = f"{MOONID_BASE}/account/login/?next=/api/account/connect/{MOONID_CONNECT_ID}/"
MOONID_CONNECT_URL = f"{MOONID_BASE}/api/account/connect/{MOONID_CONNECT_ID}/"

# Secrets
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
MG_USERNAME = os.environ.get("MG_USERNAME", "")
MG_PASSWORD = os.environ.get("MG_PASSWORD", "")
MG_COOKIE = os.environ.get("MG_COOKIE", "")  # optionaler direkter PHPSESSID Cookie auf int3 Domain

SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": MOONID_BASE + "/",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

MOTIVATION = [
    "Wolves assemble. Heute gab es Beute.",
    "Rudel aufwachen. Frische Trophäen liegen auf dem Tisch.",
    "Voller Mond, volle Taschen.",
    "Rudelbericht. Das Protokoll für heute steht.",
    "Wolves, hört zu. Hier kommt die Beute."
]

def read_players():
    default_players = [
        "[DDoV] Slevin",
        "[DDoV] Samurai Warrior",
        "[DDoV] rL.pa1n",
        "Desert Storm",
        "[DDoV] Bundy",
        "[DDoV] Mephisto",
        "[DDoV] Therapist",
        "[DDoV] Dioseph",
        "[DDoV] Breakout",
    ]
    if os.path.exists(PLAYERS_FILE):
        with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
            return names
    return default_players

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"date": None, "players": {}}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def clean_int(text):
    if text is None:
        return 0
    t = re.sub(r"[^\d]", "", text)
    return int(t) if t else 0

def login_via_moonid(session: requests.Session):
    # Loginseite holen und CSRF plus next übernehmen
    r = session.get(MOONID_LOGIN_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    form = None
    for f in soup.select("form"):
        if f.select_one("input[name='username']") and f.select_one("input[name='password']"):
            form = f
            break
    if form is None:
        raise RuntimeError("Kein Loginformular gefunden")

    action = form.get("action") or "/account/login/"
    login_url = requests.compat.urljoin(r.url, action)

    payload = {}
    for inp in form.select("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "").lower()
        val = inp.get("value") or ""
        if typ in ("hidden", "submit"):
            payload[name] = val

    payload["username"] = MG_USERNAME
    payload["password"] = MG_PASSWORD

    # Login absenden
    r2 = session.post(login_url, data=payload, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r2.raise_for_status()

    # Explizit den Connect Endpoint aufrufen, damit Cookies auf int3 gesetzt werden
    r3 = session.get(MOONID_CONNECT_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r3.raise_for_status()

    # Jetzt sollte die Session auf int3 aktiv sein
    r4 = session.get(HIGHSCORE_URL, headers={"User-Agent": SESSION_HEADERS["User-Agent"]}, timeout=30, allow_redirects=True)
    r4.raise_for_status()
    if "Logout" not in r4.text and "logout" not in r4.text:
        raise RuntimeError("Login okay, aber Session auf Spielserver nicht aktiv. Prüfe MG_CONNECT_ID oder setze MG_COOKIE als Fallback.")

def fetch_highscore(session: requests.Session):
    r = session.get(HIGHSCORE_URL, headers={"User-Agent": SESSION_HEADERS["User-Agent"]}, timeout=30)
    r.raise_for_status()
    return r.text

def parse_table(html):
    soup = BeautifulSoup(html, "lxml")
    candidate_tables = soup.select("table")
    if not candidate_tables:
        raise RuntimeError("Keine Tabelle gefunden.")

    def header_map(table):
        headers = [th.get_text(strip=True) for th in table.select("tr th")]
        if not headers:
            first = table.select_one("tr")
            headers = [td.get_text(strip=True) for td in first.select("td")] if first else []
        mapping = {}
        for idx, h in enumerate(headers):
            t = h.strip().lower()
            if "name" in t:
                mapping["name"] = idx
            elif "lvl" in t:
                mapping["level"] = idx
            elif "loot" in t:
                mapping["loot"] = idx
            elif t == "w" or t.startswith("w "):
