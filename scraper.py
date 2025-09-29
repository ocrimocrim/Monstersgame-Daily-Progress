import os, sys, re, html
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

# ==== Konfiguration aus Secrets/ENV ====
HIGHSCORE_URL   = os.getenv("MG_HIGHSCORE_URL", "").strip()
WATCHLIST       = [x.strip() for x in os.getenv("MG_WATCHLIST","").split(",") if x.strip()]
USERNAME        = os.getenv("MG_USERNAME", "").strip()
PASSWORD        = os.getenv("MG_PASSWORD", "").strip()

SESSIONID       = os.getenv("MG_SESSIONID", "").strip()      # Cookie für moonid.net (SSO)
PHPSESSID       = os.getenv("MG_PHPSESSID", "").strip()      # (optional) Cookie auf intX.monstersgame.moonid.net
CSRFTOKEN       = os.getenv("MG_CSRFTOKEN", "").strip()      # (optional) Cookie auf moonid.net
CONNECT_ID      = os.getenv("MG_CONNECT_ID", "").strip()     # (optional) verstecktes Feld, falls gebraucht
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
DEBUG           = os.getenv("MG_DEBUG", "0").strip() == "1"

if not HIGHSCORE_URL:
    print("Fehler: MG_HIGHSCORE_URL ist nicht gesetzt.", file=sys.stderr)
    sys.exit(1)

# ==== Helpers ====
def dbg(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")

def host_of(url: str) -> str:
    return urlparse(url).netloc

def scheme_of(url: str) -> str:
    return urlparse(url).scheme

def moonid_root(url: str) -> str:
    # egal von welcher Instanz -> SSO ist auf moonid.net
    return f"{scheme_of(url)}://moonid.net"

def biggest_table_html(html_text: str) -> str|None:
    soup = BeautifulSoup(html_text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None
    tables.sort(key=lambda t: len(t.find_all("tr")), reverse=True)
    return str(tables[0])

def contains_highscore_markers(html_text: str) -> bool:
    # Erkennungsmerkmal: viele showuser-Links oder typische Highscore-Spalten
    if "index.php?ac=showuser" in html_text:
        return True
    soup = BeautifulSoup(html_text, "html.parser")
    heads = [th.get_text(strip=True).lower() for th in soup.find_all("th")]
    wanted = {"lvl", "loot", "bites", "w", "l", "gold", "name:"}
    return any(any(w in h for w in wanted) for h in heads)

def post_discord(msg: str):
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=15)
    except Exception as e:
        dbg(f"Discord-Post fehlgeschlagen: {e}")

# ==== Session vorbereiten ====
s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.8",
    "Connection": "keep-alive",
})

HS_HOST = host_of(HIGHSCORE_URL)
SSO_HOST = "moonid.net"
SCHEME = scheme_of(HIGHSCORE_URL)

# Cookies setzen (wenn vorhanden)
if SESSIONID:
    # moonid.net (SSO)
    for dom in [SSO_HOST, f".{SSO_HOST}"]:
        s.cookies.set("sessionid", SESSIONID, domain=dom, path="/")
    dbg("Cookie gesetzt: sessionid @ moonid.net")

if CSRFTOKEN:
    for dom in [SSO_HOST, f".{SSO_HOST}"]:
        s.cookies.set("csrftoken", CSRFTOKEN, domain=dom, path="/")
    dbg("Cookie gesetzt: csrftoken @ moonid.net")

if PHPSESSID:
    # direkt für die Spielinstanz
    for dom in [HS_HOST, f".{HS_HOST}"]:
        s.cookies.set("PHPSESSID", PHPSESSID, domain=dom, path="/")
    dbg(f"Cookie gesetzt: PHPSESSID @ {HS_HOST}")

def fetch(url, allow_redirects=True):
    r = s.get(url, timeout=30, allow_redirects=allow_redirects)
    dbg(f"GET {url} -> {r.status_code} {('REDIR->'+r.headers.get('Location','')) if r.is_redirect else ''}")
    return r

def try_highscore():
    r = fetch(HIGHSCORE_URL)
    text = r.text or ""
    if contains_highscore_markers(text):
        tbl = biggest_table_html(text) or ""
        if tbl:
            print(tbl)
            return True
    return False

def attempt_login():
    # 1) Login-Seite SSO auf moonid.net
    login_base = moonid_root(HIGHSCORE_URL)
    login_url_candidates = [
        f"{login_base}/login",
        f"{login_base}/de/login",
        f"{login_base}/en/login",
        f"{login_base}/account/login",
    ]
    # Seite aufrufen um Tokens/Form-Felder zu bekommen
    last_get = None
    for url in login_url_candidates:
        try:
            last_get = fetch(url)
            if last_get.status_code in (200, 302):
                break
        except Exception:
            continue
    if not last_get or last_get.status_code >= 400:
        dbg("Login-Seite nicht erreichbar.")
        return False

    soup = BeautifulSoup(last_get.text, "html.parser")
    form = soup.find("form")
    if not form:
        dbg("Kein <form> auf Login-Seite gefunden – SSO kann anderes Layout haben.")
        form_action = last_get.url
    else:
        form_action = form.get("action") or last_get.url
        form_action = urljoin(last_get.url, form_action)

    # Feldnamen raten + versteckte Felder übernehmen
    data = {}
    for inp in soup.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        data[name] = inp.get("value", "")

    # mögliche Benutzernamen-/Passwort-Feldnamen abdecken
    for k in ["username", "login", "user", "email", "email_or_username"]:
        if k in data or form:
            data[k] = USERNAME
            break
    else:
        data["username"] = USERNAME

    for k in ["password", "pass", "pw"]:
        if k in data or form:
            data[k] = PASSWORD
            break
    else:
        data["password"] = PASSWORD

    if CSRFTOKEN and "csrftoken" in [i.get("name") for i in soup.find_all("input")]:
        data["csrftoken"] = CSRFTOKEN
    if CONNECT_ID:
        data.setdefault("connect_id", CONNECT_ID)

    headers = {
        "Referer": last_get.url,
        "Origin": f"{SCHEME}://{SSO_HOST}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        r = s.post(form_action, data=data, headers=headers, timeout=30, allow_redirects=True)
        dbg(f"POST {form_action} -> {r.status_code} | final: {r.url}")
    except Exception as e:
        dbg(f"POST-Login fehlgeschlagen: {e}")
        return False

    # Nach Login die Highscore-Seite erneut aufrufen
    return try_highscore()

# ==== Ablauf ====
# 1) Direkt versuchen (evtl. reicht Session/Cookies)
if try_highscore():
    sys.exit(0)

# 2) Wenn fehlgeschlagen und Username/Passwort vorhanden -> Login-Fallback
if USERNAME and PASSWORD:
    ok = attempt_login()
    if ok:
        sys.exit(0)

print("Login fehlgeschlagen. Cookie + Credentials halfen nicht.")
sys.exit(0)  # nicht als Fehler beenden, damit der Job "grün" bleibt
