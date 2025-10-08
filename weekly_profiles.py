import os, re, json, requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup

BASE_URL = "https://int3.monstersgame.moonid.net"
STATE_PATH = "data/profiles_state.json"
PLAYERS_FILE = "data/player_urls.txt"

# Secrets aus Repository Settings
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
MG_USERNAME = os.getenv("MG_USERNAME", "")
MG_PASSWORD = os.getenv("MG_PASSWORD", "")
MG_SESSIONID = os.getenv("MG_SESSIONID", "")
MG_PHPSESSID = os.getenv("MG_PHPSESSID", "")
MG_COOKIE = os.getenv("MG_COOKIE", "")
MG_CSRFTOKEN = os.getenv("MG_CSRFTOKEN", "")
MG_CONNECT_ID = os.getenv("MG_CONNECT_ID", "")
MG_DEBUG = os.getenv("MG_DEBUG", "0")

SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8",
    "Connection": "keep-alive",
}

def dbg(msg):
    if MG_DEBUG == "1":
        print(msg)

def ensure_dirs():
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"week": "", "players": {}}

def save_state(state):
    ensure_dirs()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def to_int(numstr):
    t = re.sub(r"[^\d]", "", numstr or "")
    return int(t) if t else 0

def read_players():
    players = []
    if not os.path.exists(PLAYERS_FILE):
        return players
    with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            m = re.match(r"^(https?://\S+)\s+\((.+)\)$", ln)
            if m:
                url, name = m.group(1), m.group(2).strip()
            elif "," in ln:
                name, url = [x.strip() for x in ln.split(",", 1)]
            else:
                url, name = ln, ln
            players.append({"name": name, "url": url})
    return players

def set_known_cookies(session):
    if MG_PHPSESSID:
        session.cookies.set("PHPSESSID", MG_PHPSESSID, domain="int3.monstersgame.moonid.net")
    if MG_SESSIONID:
        session.cookies.set("sessionid", MG_SESSIONID, domain="int3.monstersgame.moonid.net")
    if MG_COOKIE and not MG_PHPSESSID:
        session.cookies.set("PHPSESSID", MG_COOKIE, domain="int3.monstersgame.moonid.net")

def parse_profile(html):
    soup = BeautifulSoup(html, "html.parser")

    def table_after(header_text):
        hdr = None
        for h in soup.find_all("div", class_="headerRow"):
            if header_text.lower() in h.get_text(strip=True).lower():
                hdr = h
                break
        if not hdr:
            return None
        cont = hdr.find_next("div", class_="pageContent")
        return cont.find("table") if cont else None

    attrs = {"STR": 0, "DEF": 0, "AGI": 0, "STA": 0, "DEX": 0}
    t_attr = table_after("The attributes of")
    if t_attr:
        for tr in t_attr.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            label = tds[0].get_text(strip=True)
            valtxt = tds[1].get_text(" ", strip=True)
            m = re.search(r"\((\d+)\)", valtxt)
            val = int(m.group(1)) if m else to_int(valtxt)
            if "Strength" in label:
                attrs["STR"] = val
            elif "Defence" in label:
                attrs["DEF"] = val
            elif "Agility" in label:
                attrs["AGI"] = val
            elif "Stamina" in label:
                attrs["STA"] = val
            elif "Dexterity" in label:
                attrs["DEX"] = val

    stats = {"F": 0, "Win": 0, "Lose": 0, "GoldPlus": 0, "GoldMinus": 0, "PewPewPlus": 0}
    t_stats = table_after("Statistics")
    if t_stats:
        for tr in t_stats.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            label = tds[0].get_text(strip=True)
            valtxt = tds[1].get_text(" ", strip=True)
            if "Fights" in label:
                stats["F"] = to_int(valtxt)
            elif "Victories" in label:
                stats["Win"] = to_int(valtxt)
            elif "Defeats" in label:
                stats["Lose"] = to_int(valtxt)
            elif "Gold gained" in label:
                stats["GoldPlus"] = to_int(valtxt)
            elif "Gold lost" in label:
                stats["GoldMinus"] = to_int(valtxt)
            elif "Damage to enemies" in label:
                stats["PewPewPlus"] = to_int(valtxt)
    return attrs, stats

def fetch_html(session, url):
    r = session.get(url, headers=SESSION_HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def send_discord(text):
    if not DISCORD_WEBHOOK:
        print(text)
        return
    parts = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 1900:
            parts.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    if cur:
        parts.append(cur)
    for p in parts:
        requests.post(DISCORD_WEBHOOK, json={"content": p}, timeout=30)

def main():
    players = read_players()
    if not players:
        print("Keine Spieler gefunden.")
        return

    ensure_dirs()
    state = load_state()
    iso = datetime.now(timezone.utc).isocalendar()
    week_str = f"{iso.year}-W{iso.week:02d}"

    # Baseline erkennen: noch keine Spieler im gespeicherten Zustand
    prev = state.get("players", {})
    baseline_mode = not bool(prev)

    session = requests.Session()
    set_known_cookies(session)

    deltas, ranks = {}, []
    current = {}

    for p in players:
        html = fetch_html(session, p["url"])
        attrs, stats = parse_profile(html)
        current[p["name"]] = {"attrs": attrs, "stats": stats}

        if not baseline_mode:
            old = prev.get(p["name"], {"attrs": attrs, "stats": stats})
            da = {k: attrs[k] - old["attrs"].get(k, 0) for k in ["STR","DEF","AGI","STA","DEX"]}
            ds = {k: stats[k] - old["stats"].get(k, 0) for k in ["F","Win","Lose","GoldPlus","GoldMinus","PewPewPlus"]}
            deltas[p["name"]] = {"attrs": da, "stats": ds}
            ranks.append((p["name"], da["STR"] + da["DEF"] + da["AGI"] + da["STA"]))

    # Erstlauf: nur Baseline speichern, nichts posten
    if baseline_mode:
        new_state = {"week": week_str, "players": current}
        save_state(new_state)
        print("Baseline erfasst und gespeichert in data/profiles_state.json.")
        return

    # Regulärer Wochenreport mit Deltas
    ranks.sort(key=lambda x: x[1], reverse=True)

    lines = [f"MG Weekly Report {week_str}", ""]
    for name, d in deltas.items():
        a, s = d["attrs"], d["stats"]
        tot = a["STR"] + a["DEF"] + a["AGI"] + a["STA"]
        lines.append(f"{name} gained {a['STR']} Str, {a['DEF']} Def, {a['AGI']} Agi, {a['STA']} Sta, {a['DEX']} Dex. Total {tot}")
    lines.append("")
    for name, d in deltas.items():
        s = d["stats"]
        lines.append(f"{name} F {s['F']}, Win {s['Win']}, Lose {s['Lose']}, Gold+ {s['GoldPlus']}, Gold- {s['GoldMinus']}, PewPew+ {s['PewPewPlus']}")
    lines.append("")
    lines.append("Ranking:")
    for pos, (name, val) in enumerate(ranks, 1):
        lines.append(f"{pos}. {name} {val}")

    send_discord("\n".join(lines))

    # neuen Stand speichern
    new_state = {"week": week_str, "players": current}
    save_state(new_state)
    print("Weekly Report gesendet und gespeichert.")

if __name__ == "__main__":
    main()
