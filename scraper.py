import os
import re
import unicodedata
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

def norm(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ")  # no-break space
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def parse_int(s: str) -> int:
    if s is None:
        return 0
    # nur Ziffern behalten (tausenderpunkte/kommas, wörter wie "Gold" entfernen)
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits.isdigit() else 0

def load_watchlist() -> list[str]:
    # 1) players.txt (eine Zeile pro Name)
    if os.path.exists("players.txt"):
        with open("players.txt", "r", encoding="utf-8") as f:
            rows = [r.strip() for r in f.readlines()]
        names = [r for r in rows if r]
        if names:
            return names

    # 2) Fallback: ENV MG_WATCHLIST (Komma-getrennt)
    wl = os.environ.get("MG_WATCHLIST", "").strip()
    if wl:
        return [x.strip() for x in wl.split(",") if x.strip()]

    return []

def fetch_html(url: str, sessionid: str | None, csrftoken: str | None) -> str:
    sess = requests.Session()
    # ein paar harmlose headers
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de,en;q=0.9",
    })
    cookies = {}
    if sessionid:
        cookies["sessionid"] = sessionid
    if csrftoken:
        cookies["csrftoken"] = csrftoken

    resp = sess.get(url, cookies=cookies, timeout=30)
    resp.raise_for_status()
    return resp.text

def parse_table(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    # Alle Zeilen mit einem Spielerlink (showuser)
    for tr in soup.select("tr"):
        a = tr.select_one('a[href*="showuser"]')
        if not a:
            continue

        tds = tr.find_all("td")
        if len(tds) < 10:
            # falls die Struktur mal abweicht, nächste Zeile
            continue

        # Spalten laut deinem Dump:
        # 0: Rank
        # 1: Race (IMG alt = "Vampire"/"Werewolf")
        # 2: Name (A-Text)
        # 3: Lvl
        # 4: Loot
        # 5: Bites
        # 6: W
        # 7: L
        # 8: Anc. +
        # 9: Gold +
        race_img = tds[1].find("img")
        race = race_img["alt"].strip() if race_img and race_img.has_attr("alt") else ""

        name = a.get_text(strip=True)
        item = {
            "rank": parse_int(tds[0].get_text()),
            "race": race,
            "name": name,
            "lvl": parse_int(tds[3].get_text()),
            "loot": parse_int(tds[4].get_text()),
            "bites": parse_int(tds[5].get_text()),
            "wins": parse_int(tds[6].get_text()),
            "losses": parse_int(tds[7].get_text()),
            "anc_plus": parse_int(tds[8].get_text()),
            "gold_plus": parse_int(tds[9].get_text()),
        }
        out.append(item)
    return out

def find_matches(rows: list[dict], watchlist: list[str]) -> list[dict]:
    want = {norm(n): n for n in watchlist}  # map normalized -> original
    hits = []
    for r in rows:
        if norm(r["name"]) in want:
            hits.append(r)
    # nach Rang sortieren, wenn vorhanden, sonst Name
    hits.sort(key=lambda x: (x["rank"] if x["rank"] else 10**9, norm(x["name"])))
    return hits

def fmt_row(r: dict) -> str:
    return (f"#{r['rank']:>2} {r['race']:<8} {r['name']} | "
            f"Lvl {r['lvl']} | Loot {r['loot']:,} | Bites {r['bites']:,} | "
            f"W {r['wins']:,} / L {r['losses']:,} | Anc+ {r['anc_plus']:,} | Gold+ {r['gold_plus']:,}"
            ).replace(",", ".")

def post_discord(webhook: str, content: str):
    try:
        resp = requests.post(webhook, json={"content": content}, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Discord-Webhook fehlgeschlagen: {e}")

def main():
    url = os.environ.get("MG_HIGHSCORE_URL", "").strip()
    if not url:
        raise SystemExit("MG_HIGHSCORE_URL fehlt.")

    watchlist = load_watchlist()
    if not watchlist:
        print("Hinweis: Watchlist leer. Lege 'players.txt' an (eine Zeile pro Name) "
              "oder setze MG_WATCHLIST.")
        watchlist = []

    sessionid = os.environ.get("MG_SESSIONID", "").strip() or None
    csrftoken = os.environ.get("MG_CSRFTOKEN", "").strip() or None
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip() or None

    html = fetch_html(url, sessionid, csrftoken)
    rows = parse_table(html)
    hits = find_matches(rows, watchlist)

    # Kopfzeile
    now = datetime.now(timezone.utc).astimezone()
    header = f"Wolves, hört zu. Hier kommt die Beute.\nDatum {now:%Y-%m-%d}"

    if not hits:
        body = "Keine Treffer in der Watchlist. Prüfe Spielernamen oder Parser."
    else:
        body = "\n".join(fmt_row(r) for r in hits)

    msg = f"{header}\n{body}"
    print(msg)
    if webhook:
        post_discord(webhook, msg)

if __name__ == "__main__":
    main()
