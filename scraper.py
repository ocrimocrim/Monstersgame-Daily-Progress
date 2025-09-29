import os, json, random, re, sys, pathlib
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup
import pandas as pd

# Basis
BASE_URL = "https://int3.monstersgame.moonid.net"
HIGHSCORE_URL = BASE_URL + "/index.php?ac=highscore&sac=spieler&highrasse=0&count=0&filter=gold_won&direction="
STATE_PATH = "data/state.json"
DEBUG_HTML = "data/last_highscore.html"
PLAYERS_FILE = "players.txt"

# moonID
MOONID_BASE = "https://moonid.net"
MG_CONNECT_ID = os.getenv("MG_CONNECT_ID") or "240"
MOONID_LOGIN_URL  = f"{MOONID_BASE}/account/login/?next=/api/account/connect/{MG_CONNECT_ID}/"
MOONID_CONNECT_URL = f"{MOONID_BASE}/api/account/connect/{MG_CONNECT_ID}/"

# Secrets
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
MG_USERNAME = os.environ.get("MG_USERNAME", "")
MG_PASSWORD = os.environ.get("MG_PASSWORD", "")
MG_COOKIE = os.environ.get("MG_COOKIE", "")  # optional

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
            return [line.strip() for line in f if line.strip()]
    return default_players

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"date": None, "players": {}}

def save_state(state):
    pathlib.Path("data").mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def clean_int(text):
    if text is None:
        return 0
    t = re.sub(r"[^\d]", "", str(text))
    return int(t) if t else 0

def login_via_moonid(session: requests.Session):
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

    r2 = session.post(login_url, data=payload, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r2.raise_for_status()

    r3 = session.get(MOONID_CONNECT_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r3.raise_for_status()

    r4 = session.get(HIGHSCORE_URL, headers={"User-Agent": SESSIO_
