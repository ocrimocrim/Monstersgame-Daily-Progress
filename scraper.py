import os, json, random, re, sys
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

# Basis
BASE_URL = "https://int3.monstersgame.moonid.net"
HIGHSCORE_URL = BASE_URL + "/index.php?ac=highscore&sac=spieler&highrasse=0&count=0&filter=gold_won&direction="
STATE_PATH = "data/state.json"
PLAYERS_FILE = "players.txt"

# moonID Login
MOONID_BASE = "https://moonid.net"
# Die Connect-ID ist pro Spielserver unterschiedlich. 240 passt zu deinem Beispiel.
MOONID_CONNECT_ID = os.environ.get("MG_CONNECT_ID", "240")
MOONID_LOGIN_URL = f"{MOONID_BASE}/account/login/?next=/api/account/connect/{MOONID_CONNECT_ID}/"

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
MG_USERNAME = os.environ.get("MG_USERNAME", "")
MG_PASSWORD = os.environ.get("MG_PASSWORD", "")
# Optionaler direkter Session-Cookie, falls nötig. Name ist typischerweise PHPSESSID auf der Spiel-Domain.
MG_COOKIE = os.environ.get("MG_COOKIE", "")

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
    # Login-Seite holen, CSRF einsammeln
    r = session.get(MOONID_LOGIN_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    form = None
    # bevorzugt das Formular mit action /account/login/
    for f in soup.select("form"):
        action = (f.get("action") or "").lower()
        if "/account/login/" in action or f.select_one("input[type=password]"):
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
        # CSRF und next übernehmen
        if typ in ("hidden", "submit"):
            payload[name] = val

    # Feldnamen aus dem HTML: username, password
    user_input = form.select_one("input[name='username']") or form.select_one("input[name*=user]")
    pass_input = form.select_one("input[name='password']") or form.select_one("input[type=password]")
    if not user_input or not pass_input:
        raise RuntimeError("Benutzer oder Passwortfeld nicht erkannt")

    payload[user_input.get("name")] = MG_USERNAME
    payload[pass_input.get("name")] = MG_PASSWORD

    # Absenden und Weiterleitungen bis zum Gameserver folgen
    r2 = session.post(login_url, data=payload, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r2.raise_for_status()

    # Nach dem Login leitet moonID auf /api/account/connect/<id>/ und dann auf die Spiel-Domain
    # Wir prüfen mit einem Abruf der Highscore-Seite, ob Cookies gesetzt wurden
    r3 = session.get(HIGHSCORE_URL, headers={"User-Agent": SESSION_HEADERS["User-Agent"]}, timeout=30, allow_redirects=True)
    r3.raise_for_status()
    html = r3.text
    # heuristische Prüfung auf eingeloggt
    if "Logout" not in html and "logout" not in html:
        raise RuntimeError("Login okay, aber Session auf Spielserver nicht aktiv. Prüfe MG_CONNECT_ID oder Credentials.")

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
                mapping["wins"] = idx
            elif t == "l" or t.startswith("l "):
                mapping["losses"] = idx
            elif "anc" in t:
                mapping["anc"] = idx
            elif "gold" in t:
                mapping["gold"] = idx
        return mapping

    chosen, cols = None, None
    for tb in candidate_tables:
        m = header_map(tb)
        if {"name","level","loot","wins","losses","anc","gold"} <= set(m.keys()):
            chosen, cols = tb, m
            break
    if chosen is None:
        chosen = max(candidate_tables, key=lambda t: len(t.select("tr")))
        cols = header_map(chosen)

    data = []
    for tr in chosen.select("tr"):
        tds = tr.select("td")
        if not tds or len(tds) < max(cols.values()) + 1:
            continue
        def get(col):
            return tds[cols[col]].get_text(strip=True) if col in cols else ""
        row = {
            "name": get("name"),
            "level": clean_int(get("level")),
            "loot": clean_int(get("loot")),
            "wins": clean_int(get("wins")),
            "losses": clean_int(get("losses")),
            "anc": clean_int(get("anc")),
            "gold": clean_int(get("gold")),
        }
        if row["name"]:
            data.append(row)
    return data

def pick_players(rows, watchlist):
    names_norm = {n.strip(): n.strip() for n in watchlist}
    out = {}
    for r in rows:
        if r["name"] in names_norm:
            out[r["name"]] = r
    return out

def build_message(today, prev_players, today_players):
    report = []
    ranked = []
    for name, now in today_players.items():
        prev = prev_players.get(name, {})
        d_gold = now["gold"] - prev.get("gold", 0)
        d_wins = now["wins"] - prev.get("wins", 0)
        d_losses = now["losses"] - prev.get("losses", 0)
        d_anc = now["anc"] - prev.get("anc", 0)
        d_lvl = now["level"] - prev.get("level", 0)

        line = f"{name} looted {d_gold} Gold, won {d_wins} fights, lost {d_losses} fights."
        extras = []
        if d_lvl > 0:
            extras.append(f"Level up +{d_lvl}")
        if d_anc > 0:
            extras.append(f"Ancestor fights +{d_anc}")
        if extras:
            line += " " + " ".join(extras)
        report.append((name, d_gold, line))
        ranked.append((name, d_gold))

    top_name, top_gold = None, None
    if ranked:
        top_name, top_gold = max(ranked, key=lambda x: x[1])

    intro = random.choice(MOTIVATION)
    date_str = today.strftime("%Y-%m-%d")
    lines = [f"{intro}", f"Datum {date_str}"]
    if top_name is not None:
        lines.append(f"Champion des Tages ist {top_name} mit {top_gold} Gold.")
    for _, _, line in sorted(report, key=lambda x: x[0].lower()):
        lines.append(line)
    return "\n".join(lines)

def send_discord(msg):
    if not DISCORD_WEBHOOK:
        print("Kein DISCORD_WEBHOOK gesetzt. Nachricht wird nur ausgegeben.")
        print(msg)
        return
    payload = {"content": msg}
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=30)
    r.raise_for_status()

def main():
    if not MG_USERNAME or not MG_PASSWORD:
        raise RuntimeError("MG_USERNAME und MG_PASSWORD müssen als Secrets gesetzt sein.")

    watchlist = read_players()

    with requests.Session() as s:
        s.headers.update(SESSION_HEADERS)

        if MG_COOKIE:
            # Optionaler direkter Cookie auf der Spiel-Domain
            s.cookies.set("PHPSESSID", MG_COOKIE, domain="int3.monstersgame.moonid.net")
        else:
            login_via_moonid(s)

        html = fetch_highscore(s)
        rows = parse_table(html)
        today_players = pick_players(rows, watchlist)

    state = load_state()
    prev_players = state.get("players", {})
    today = datetime.now(timezone.utc)

    msg = build_message(today, prev_players, today_players)
    send_discord(msg)

    new_players = {name: today_players[name] for name in today_players}
    new_state = {
        "date": today.isoformat(),
        "players": new_players
    }
    save_state(new_state)
    print("Protokoll gesendet und State gespeichert.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fehler {e}")
        sys.exit(1)
