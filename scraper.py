#!/usr/bin/env python3
import os
import sys
import re
import json
import unicodedata
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if (v is not None and str(v).strip() != "") else default

MG_URL         = env("MG_HIGHSCORE_URL")
MG_SESSIONID   = env("MG_SESSIONID")
MG_CSRFTOKEN   = env("MG_CSRFTOKEN")
DISCORD_WEBHOOK= env("DISCORD_WEBHOOK")

PLAYERS_FILE   = env("PLAYERS_FILE", "players.txt")
MG_WATCHLIST   = env("MG_WATCHLIST")  # optional Fallback, kommasepariert

if not MG_URL:
    print("Fehler: MG_HIGHSCORE_URL ist nicht gesetzt.", file=sys.stderr)
    sys.exit(1)
if not MG_SESSIONID:
    print("Fehler: MG_SESSIONID (Cookie) ist nicht gesetzt. Bitte aus den DevTools kopieren und als Secret hinterlegen.", file=sys.stderr)
    sys.exit(1)

def normalize_name(s: str) -> str:
    """robuste Normalisierung: Unicode, Whitespaces, Case, Sonderzeichen/Klammern bleiben erhalten."""
    if s is None:
        return ""
    # Unicode normalisieren
    s = unicodedata.normalize("NFKC", s)
    # &nbsp; und harte Spaces auf normale Spaces
    s = s.replace("\xa0", " ")
    # Trim + Mehrfachspaces zu einem Space
    s = " ".join(s.strip().split())
    # Case-insensitive vergleichen
    return s.casefold()

def load_watchlist() -> List[str]:
    # 1) players.txt (eine Zeile pro Name)
    names: List[str] = []
    if os.path.exists(PLAYERS_FILE):
        with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if t:
                    names.append(t)
    # 2) optionaler Fallback: MG_WATCHLIST="Name1, Name2, ..."
    if not names and MG_WATCHLIST:
        names.extend([x.strip() for x in MG_WATCHLIST.split(",") if x.strip()])
    if not names:
        print("Warnung: Keine Watchlist gefunden (weder players.txt noch MG_WATCHLIST).", file=sys.stderr)
    return names

def fetch_highscore_html() -> str:
    sess = requests.Session()
    # Cookies setzen (Session-Login)
    sess.cookies.set("sessionid", MG_SESSIONID, domain=".monstersgame.moonid.net")
    if MG_CSRFTOKEN:
        sess.cookies.set("csrftoken", MG_CSRFTOKEN, domain=".monstersgame.moonid.net")

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    r = sess.get(MG_URL, headers=headers, timeout=30)
    r.raise_for_status()
    html = r.text
    return html

def parse_rows(html: str) -> List[Dict[str, str]]:
    """
    Parsen der Highscore-Tabelle.
    Erwartete Struktur: <tr> ... <td>#</td> <td>(Race Icon)</td> <td><a>NAME</a></td> <td>Lvl</td> ... etc.
    """
    soup = BeautifulSoup(html, "html.parser")

    # finde die Tabelle über die Head-Zeile (Race / Name / Lvl ...)
    header_td = soup.find("td", string=re.compile(r"\bName:\b", re.I))
    if not header_td:
        # Alternative: nach dem "Your Rank" Block suchen und dann folgendes <tr class="bghighscore"> nehmen
        header_tr = soup.find("tr", class_="bghighscore")
    else:
        header_tr = header_td.find_parent("tr")

    table_rows: List[Dict[str, str]] = []

    # alle folgenden <tr>, bis der nächste Block kommt
    for tr in (header_tr.find_next_siblings("tr") if header_tr else soup.find_all("tr")):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        # erste Zelle enthält Rangnummer (z.B. "1.")
        rank_text = tds[0].get_text(strip=True)
        if not rank_text or not re.match(r"^\d+\.$", rank_text):
            # nicht die Datensatz-Zeile
            continue

        # Name sitzt in der 3. Zelle (Index 2), meistens als <a>
        name_el = tds[2].find("a")
        name = name_el.get_text(strip=True) if name_el else tds[2].get_text(strip=True)

        # Sicher weitere Spalten holen, wenn vorhanden
        def td(idx: int) -> str:
            return tds[idx].get_text(" ", strip=True) if idx < len(tds) else ""

        row = {
            "rank": rank_text.rstrip("."),
            "name": name,
            "lvl": td(3),
            "loot": td(4),
            "bites": td(5),
            "w": td(6),
            "l": td(7),
            "anc_plus": td(8),
            "gold_plus": td(9),
        }
        table_rows.append(row)

    return table_rows

def post_to_discord(content: str) -> None:
    if not DISCORD_WEBHOOK:
        print("(Kein DISCORD_WEBHOOK gesetzt – überspringe Discord-Post.)")
        return
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"Discord-Post fehlgeschlagen: {e}", file=sys.stderr)

def main() -> int:
    watchlist_raw = load_watchlist()
    watch_norm = {normalize_name(n) for n in watchlist_raw}

    html = fetch_highscore_html()
    rows = parse_rows(html)

    if not rows:
        print("Fehler: Konnte keine Highscore-Zeilen parsen – ist die Session gültig / Login ok?", file=sys.stderr)
        return 1

    # Matchen (robust gegen Leerzeichen, Klammern, Schreibweise)
    hits = []
    for r in rows:
        n_norm = normalize_name(r["name"])
        if n_norm in watch_norm:
            hits.append(r)

    # Ausgabe/Discord
    header = "Wolves, hört zu. Hier kommt die Beute."
    date_line = f"Datum {os.environ.get('GITHUB_RUN_STARTED_AT') or ''}".strip()
    if not date_line or date_line == "Datum":
        from datetime import datetime, timezone
        date_line = "Datum " + datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not hits:
        msg = f"{header}\n{date_line}\nKeine Treffer in der Watchlist. Prüfe Spielernamen oder Parser."
        print(msg)
        post_to_discord(msg)
        return 0  # kein Fund ist KEIN Fehler

    # hübsch formatieren
    lines = [header, date_line, "Gefundene Spieler:"]
    for h in hits:
        lines.append(
            f"#{h['rank']:>3} | {h['name']} | Lvl {h['lvl']} | Loot {h['loot']} | W {h['w']} | L {h['l']} | Gold+ {h['gold_plus']}"
        )
    msg = "\n".join(lines)
    print(msg)
    post_to_discord(msg)
    return 0

if __name__ == "__main__":
    sys.exit(main())
