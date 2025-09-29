import os, json, random, re, sys
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://int3.monstersgame.moonid.net"
HIGHSCORE_URL = BASE_URL + "/index.php?ac=highscore&sac=spieler&highrasse=0&count=0&filter=gold_won&direction="
STATE_PATH = "data/state.json"
PLAYERS_FILE = "players.txt"

# Login Endpunkt und Feldnamen ggf. anpassen falls die Seite andere Names verwendet
LOGIN_URL = BASE_URL + "/index.php"
LOGIN_USER_FIELD = "username"
LOGIN_PASS_FIELD = "password"

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
MG_USERNAME = os.environ.get("MG_USERNAME", "")
MG_PASSWORD = os.environ.get("MG_PASSWORD", "")

SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_URL + "/",
}

MOTIVATION = [
    "Wolves assemble. Heute gab es Beute.",
    "Rudel aufwachen. Frisches Blut für die Hall of Fame.",
    "Volle Monde. Starke Beutezüge heute.",
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

def login(session: requests.Session):
    # Erste Seite laden um Cookies oder Token zu bekommen
    r1 = session.get(LOGIN_URL, headers=SESSION_HEADERS, timeout=30)
    r1.raise_for_status()

    # Versuche CSRF Token aus versteckten Feldern zu greifen falls vorhanden
    token_name, token_value = None, None
    try:
        soup = BeautifulSoup(r1.text, "lxml")
        token_input = soup.select_one("input[type=hidden][name*=token], input[type=hidden][name*=csrf]")
        if token_input:
            token_name = token_input.get("name")
            token_value = token_input.get("value")
    except Exception:
        pass

    payload = {
        LOGIN_USER_FIELD: MG_USERNAME,
        LOGIN_PASS_FIELD: MG_PASSWORD,
    }
    if token_name and token_value:
        payload[token_name] = token_value

    # Die echten Feldnamen können abweichen. Wenn Login fehlschlägt, Feldnamen anpassen.
    r2 = session.post(LOGIN_URL, data=payload, headers=SESSION_HEADERS, timeout=30)
    r2.raise_for_status()

    # Prüfen ob Login durch ist. Heuristik über Konto-Navigation
    ok = "Logout" in r2.text or "logout" in r2.text
    if not ok:
        # Manche Seiten leiten erst nach dem Login weiter
        r3 = session.get(HIGHSCORE_URL, headers=SESSION_HEADERS, timeout=30)
        ok = "Logout" in r3.text or "logout" in r3.text
        if not ok:
            raise RuntimeError("Login fehlgeschlagen. Feldnamen im Script prüfen.")

def fetch_highscore(session: requests.Session):
    r = session.get(HIGHSCORE_URL, headers=SESSION_HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def parse_table(html):
    soup = BeautifulSoup(html, "lxml")

    # Nimm die erste Tabelle mit Spalten die zu den Überschriften passen
    candidate_tables = soup.select("table")
    if not candidate_tables:
        raise RuntimeError("Keine Tabelle gefunden.")

    def header_map(table):
        headers = [th.get_text(strip=True) for th in table.select("tr th")]
        if not headers:
            # Fallback falls th fehlt
            first = table.select_one("tr")
            headers = [td.get_text(strip=True) for td in first.select("td")] if first else []
        mapping = {}
        for idx, h in enumerate(headers):
            t = h.lower()
            if "name" in t:
                mapping["name"] = idx
            elif "lvl" in t:
                mapping["level"] = idx
            elif "loot" in t:
                mapping["loot"] = idx
            elif re.fullmatch(r"w\b.*", t) or t == "w":
                mapping["wins"] = idx
            elif re.fullmatch(r"l\b.*", t) or t == "l":
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
        # Nimm die größte Tabelle als Fallback
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
    # Deltas berechnen
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
            extras.append(f"Level up plus{d_lvl}")
        if d_anc > 0:
            extras.append(f"Ancestor fights plus{d_anc}")
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
    # stabile Reihenfolge nach Name
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
        login(s)
        html = fetch_highscore(s)
        rows = parse_table(html)
        today_players = pick_players(rows, watchlist)

    state = load_state()
    prev_players = state.get("players", {})
    today = datetime.now(timezone.utc)

    msg = build_message(today, prev_players, today_players)
    send_discord(msg)

    # State aktualisieren
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
        # Fehler auch an Discord geben falls gewünscht
        print(f"Fehler {e}")
        sys.exit(1)
