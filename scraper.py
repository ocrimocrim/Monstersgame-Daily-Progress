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

    r4 = session.get(HIGHSCORE_URL, headers={"User-Agent": SESSION_HEADERS["User-Agent"]}, timeout=30, allow_redirects=True)
    r4.raise_for_status()
    if "Logout" not in r4.text and "logout" not in r4.text:
        raise RuntimeError("Login okay, aber Session auf Spielserver nicht aktiv. Prüfe MG_CONNECT_ID oder nutze MG_COOKIE.")

def fetch_highscore(session: requests.Session):
    r = session.get(HIGHSCORE_URL, headers={"User-Agent": SESSION_HEADERS["User-Agent"]}, timeout=30)
    r.raise_for_status()
    return r.text

def parse_table_bs(html):
    soup = BeautifulSoup(html, "lxml")
    candidate_tables = soup.select("table")
    if not candidate_tables:
        return []

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
        return []

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

def parse_table_pandas(html):
    # Fallback: jede Tabelle lesen und passende Spalten suchen
    tables = pd.read_html(html, flavor="lxml")
    for df in tables:
        cols = [str(c).strip().lower() for c in df.columns]
        has_name = any("name" in c for c in cols)
        has_gold = any("gold" in c for c in cols)
        if not (has_name and has_gold):
            continue
        # Spaltennamen zuordnen
        def col_idx(keys):
            for i, c in enumerate(cols):
                if any(k in c for k in keys):
                    return i
            return None
        idx = {
            "name": col_idx(["name"]),
            "level": col_idx(["lvl", "level"]),
            "loot": col_idx(["loot"]),
            "wins": col_idx([" w", "wins", "w " , "w"]),
            "losses": col_idx([" l", "loss", "losses", "l " , "l"]),
            "anc": col_idx(["anc"]),
            "gold": col_idx(["gold"]),
        }
        req = all(idx[k] is not None for k in ["name","level","loot","wins","losses","anc","gold"])
        if not req:
            continue
        data = []
        for _, row in df.iterrows():
            name = str(row.iloc[idx["name"]]).strip()
            if not name or name.lower() == "name":
                continue
            data.append({
                "name": name,
                "level": clean_int(row.iloc[idx["level"]]),
                "loot": clean_int(row.iloc[idx["loot"]]),
                "wins": clean_int(row.iloc[idx["wins"]]),
                "losses": clean_int(row.iloc[idx["losses"]]),
                "anc": clean_int(row.iloc[idx["anc"]]),
                "gold": clean_int(row.iloc[idx["gold"]]),
            })
        if data:
            return data
    return []

def parse_table(html):
    rows = parse_table_bs(html)
    if rows:
        return rows
    # Fallback
    return parse_table_pandas(html)

def pick_players(rows, watchlist):
    watch = {n.strip(): True for n in watchlist}
    return {r["name"]: r for r in rows if r["name"] in watch}

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
        if d_lvl > 0:
            extras.append(f"Level up +{d_lvl}")
        if d_anc > 0:
            extras.append(f"Ancestor fights +{d_anc}")
        if extras:
            line += " " + " ".join(extras)
        report.append((name, d_gold, line))
        ranked.append((name, d_gold))

    intro = random.choice(MOTIVATION)
    date_str = today.strftime("%Y-%m-%d")
    lines = [f"{intro}", f"Datum {date_str}"]

    if ranked:
        top_name, top_gold = max(ranked, key=lambda x: x[1])
        lines.append(f"Champion des Tages ist {top_name} mit {top_gold} Gold.")
    else:
        lines.append("Keine Treffer in der Watchlist. Prüfe Spielernamen oder Parser.")

    for _, _, line in sorted(report, key=lambda x: x[0].lower()):
        lines.append(line)
    return "\n".join(lines)

def send_discord(msg):
    if not DISCORD_WEBHOOK:
        print("Kein DISCORD_WEBHOOK gesetzt. Nachricht wird nur ausgegeben.")
        print(msg)
        return
    r = requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=30)
    r.raise_for_status()

def main():
    if not MG_USERNAME or not MG_PASSWORD:
        raise RuntimeError("MG_USERNAME und MG_PASSWORD fehlen")

    watchlist = read_players()

    with requests.Session() as s:
        s.headers.update(SESSION_HEADERS)
        if MG_COOKIE:
            s.cookies.set("PHPSESSID", MG_COOKIE, domain="int3.monstersgame.moonid.net")
        else:
            login_via_moonid(s)

        html = fetch_highscore(s)

    rows = parse_table(html)
    today_players = pick_players(rows, watchlist)

    # Debug-Dump bei Problemen
    if not rows or not today_players:
        pathlib.Path("data").mkdir(parents=True, exist_ok=True)
        with open(DEBUG_HTML, "w", encoding="utf-8") as f:
            f.write(html[:500000])  # capped
        print(f"Debug gespeichert in {DEBUG_HTML}. Gefundene Gesamtzeilen {len(rows)}. Gefundene Spieler {len(today_players)}.")

    state = load_state()
    prev_players = state.get("players", {})
    today = datetime.now(timezone.utc)

    msg = build_message(today, prev_players, today_players)
    send_discord(msg)

    new_state = {"date": today.isoformat(), "players": {n: today_players[n] for n in today_players}}
    save_state(new_state)
    print("Protokoll gesendet und State gespeichert.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fehler {e}")
        sys.exit(1)
