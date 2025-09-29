import os
import re
import unicodedata
import sys
from typing import List, Dict
import requests
from bs4 import BeautifulSoup

# -------------------------
# Konfiguration (per ENV)
# -------------------------
# Pflicht: URL der Highscore-Seite (dein Beispiel von int3)
HIGHSCORE_URL = os.getenv(
    "MG_HIGHSCORE_URL",
    "https://int3.monstersgame.moonid.net/index.php?ac=highscore&vid=0",
)

# Optional: Watchlist als Komma-getrennte Liste, z.B. "[RSK] Royo, The puzzle, ((( -l-_MERCENARIOSKY_-l- )))"
WATCHLIST_RAW = os.getenv("MG_WATCHLIST", "")

# Optional: Cookies, falls Login nötig ist (in DevTools gesehen)
COOKIE_SESSIONID = os.getenv("MG_SESSIONID", "").strip()
COOKIE_CSRFTOKEN = os.getenv("MG_CSRFTOKEN", "").strip()

# Optional: Discord Webhook (wenn gesetzt, wird dorthin gepostet)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()


# -------------------------
# Hilfsfunktionen
# -------------------------
def norm_name(s: str) -> str:
    """Unicode normalisieren, Mehrfach-Leerzeichen einklappen, trimmen, Kleinschreibung."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def to_int(num: str) -> int:
    """Zahlen mit Tausendertrennzeichen in int wandeln."""
    if not num:
        return 0
    num = num.replace(".", "").replace(",", "").strip()
    return int(num) if re.fullmatch(r"\d+", num) else 0


def fetch_html(url: str) -> str:
    """HTML holen, mit optionalen Cookies."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MG-Scraper/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    cookies = {}
    if COOKIE_SESSIONID:
        cookies["sessionid"] = COOKIE_SESSIONID
    if COOKIE_CSRFTOKEN:
        cookies["csrftoken"] = COOKIE_CSRFTOKEN

    resp = requests.get(url, headers=headers, cookies=cookies, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_highscore_table(html: str) -> List[Dict]:
    """Spielerzeilen aus dem HTML extrahieren (genau wie in deinem Dump)."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    for tr in soup.find_all("tr"):
        a = tr.find("a", href=re.compile(r"showuser"))
        if not a:
            continue  # keine Spielerzeile

        tds = tr.find_all("td", class_="tdn_highscore")
        if len(tds) < 10:
            # Manche Tabellenzeilen (Überschriften etc.) haben weniger Spalten
            continue

        # Spalten laut deinem HTML:
        # 0 rank, 1 race (img alt), 2 name (a), 3 lvl, 4 loot, 5 bites, 6 W, 7 L, 8 Anc. +, 9 Gold + (Text vor <img>)
        rank_txt = tds[0].get_text(strip=True).rstrip(".")
        race_img = tds[1].find("img")
        race = race_img.get("alt").strip() if race_img and race_img.has_attr("alt") else ""
        name_raw = a.get_text()
        lvl = tds[3].get_text(strip=True)
        loot = tds[4].get_text(strip=True)
        bites = tds[5].get_text(strip=True)
        wins = tds[6].get_text(strip=True)
        losses = tds[7].get_text(strip=True)
        anc = tds[8].get_text(strip=True)

        # In der Gold-Spalte hängt ein Münz-Icon -> nur Zahl vor dem Bild nehmen
        gold_text = tds[9].get_text(" ", strip=True)
        gold_text = gold_text.split()[0] if gold_text else "0"

        row = {
            "rank": to_int(rank_txt),
            "race": race,
            "name": unicodedata.normalize("NFKC", name_raw).strip(),
            "level": to_int(lvl),
            "loot": to_int(loot),
            "bites": to_int(bites),
            "wins": to_int(wins),
            "losses": to_int(losses),
            "anc_plus": to_int(anc),
            "gold_plus": to_int(gold_text),
        }
        rows.append(row)

    return rows


def find_watchlist_hits(rows: List[Dict], watchlist: List[str]) -> List[Dict]:
    wl = {norm_name(n) for n in watchlist if n.strip()}
    if not wl:
        return []
    hits = [r for r in rows if norm_name(r["name"]) in wl]
    return hits


def fmt_gold(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def build_message(hits: List[Dict]) -> str:
    if not hits:
        return (
            "Wolves, hört zu. Hier kommt die Beute.\n"
            f"Datum {os.getenv('MG_DATE_OVERRIDE', '') or __import__('datetime').date.today()}\n"
            "Keine Treffer in der Watchlist. Prüfe Spielernamen oder Parser."
        )

    lines = [
        "Rudel aufwachen. Frische Trophäen liegen auf dem Tisch.",
        f"Datum {os.getenv('MG_DATE_OVERRIDE', '') or __import__('datetime').date.today()}",
        "",
    ]
    for r in sorted(hits, key=lambda x: x["rank"] or 10**9):
        lines.append(
            f"#{r['rank']:>2} {r['race']:<8} {r['name']} | Lvl {r['level']} | "
            f"Loot {fmt_gold(r['loot'])} | Bites {fmt_gold(r['bites'])} | "
            f"W {fmt_gold(r['wins'])} / L {fmt_gold(r['losses'])} | "
            f"Anc+ {fmt_gold(r['anc_plus'])} | Gold+ {fmt_gold(r['gold_plus'])}"
        )
    return "\n".join(lines)


def post_to_discord(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Warn] Discord-Post fehlgeschlagen: {e}", file=sys.stderr)


def main():
    try:
        html = fetch_html(HIGHSCORE_URL)
    except Exception as e:
        print(f"[Fehler] Konnte Highscore nicht laden: {e}")
        sys.exit(1)

    rows = parse_highscore_table(html)

    # Watchlist aufbauen
    watchlist = [w.strip() for w in WATCHLIST_RAW.split(",")] if WATCHLIST_RAW else []

    hits = find_watchlist_hits(rows, watchlist)

    # NIE max() auf leeren Listen -> vorher prüfen
    msg = build_message(hits)

    # Ausgabe + optional Discord
    print(msg)
    post_to_discord(msg)


if __name__ == "__main__":
    main()
