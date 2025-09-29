import os
import re
import requests
from bs4 import BeautifulSoup

# Secrets aus GitHub Actions
HIGHSCORE_URL = os.environ.get("MG_HIGHSCORE_URL")
WATCHLIST = os.environ.get("MG_WATCHLIST", "")
SESSIONID = os.environ.get("MG_SESSIONID")
USERNAME = os.environ.get("MG_USERNAME")
PASSWORD = os.environ.get("MG_PASSWORD")

session = requests.Session()

def logged_in(html_text):
    """Checkt ob Highscore-Tabelle sichtbar ist"""
    return "Your Rank" in html_text or "Your rank" in html_text

def login_with_sessionid():
    if not SESSIONID:
        return False
    session.cookies.set("sessionid", SESSIONID, domain="moonid.net")
    r = session.get(HIGHSCORE_URL)
    if logged_in(r.text):
        return r.text
    return None

def login_with_credentials():
    if not USERNAME or not PASSWORD:
        return None
    login_url = "https://moonid.net/login/"
    payload = {
        "username": USERNAME,
        "password": PASSWORD,
    }
    r = session.post(login_url, data=payload)
    if r.status_code == 200:
        # Danach Highscore aufrufen
        r2 = session.get(HIGHSCORE_URL)
        if logged_in(r2.text):
            return r2.text
    return None

def scrape_highscore(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows = []
    for tr in table.find_all("tr"):
        cols = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if cols:
            rows.append(cols)
    return rows

def normalize_name(name):
    return re.sub(r"\s+", " ", name.strip().lower())

def find_watchlist_players(rows):
    wanted = [normalize_name(n) for n in WATCHLIST.split(",") if n.strip()]
    hits = []
    for row in rows:
        for col in row:
            if normalize_name(col) in wanted:
                hits.append(row)
                break
    return hits

def main():
    html = None
    # 1. Versuch mit SessionID
    if SESSIONID:
        html = login_with_sessionid()
    # 2. Fallback mit Username/Password
    if html is None:
        html = login_with_credentials()
    if html is None:
        print("Login fehlgeschlagen. Weder Cookie noch Credentials funktionieren.")
        return

    rows = scrape_highscore(html)
    if not rows:
        print("Keine Highscore-Daten gefunden.")
        return

    hits = find_watchlist_players(rows)
    if hits:
        print("Gefundene Spieler:")
        for h in hits:
            print(h)
    else:
        print("Keine Treffer in der Watchlist. Prüfe Spielernamen oder Parser.")

if __name__ == "__main__":
    main()
