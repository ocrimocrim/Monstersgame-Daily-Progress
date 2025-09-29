SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_URL + "/",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

def login(session: requests.Session):
    # Einstieg holen
    r = session.get(BASE_URL + "/index.php", headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()

    # Formular mit Passwortfeld finden
    soup = BeautifulSoup(r.text, "lxml")
    form = None
    for f in soup.select("form"):
        if f.select_one("input[type=password]"):
            form = f
            break
    if form is None:
        # Fallback auf eventuelle Weiterleitung zu separater Loginseite
        cand = soup.select_one("a[href*='login'], a[href*='signin']")
        if cand and cand.get("href"):
            r = session.get(requests.compat.urljoin(BASE_URL, cand["href"]), headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            for f in soup.select("form"):
                if f.select_one("input[type=password]"):
                    form = f
                    break
    if form is None:
        raise RuntimeError("Kein Loginformular gefunden")

    action = form.get("action") or "/index.php"
    login_url = requests.compat.urljoin(r.url, action)

    # Eingabefelder sammeln
    payload = {}
    for inp in form.select("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "").lower()
        val = inp.get("value") or ""
        if typ in ("hidden", "submit"):
            payload[name] = val

    # Namensfelder für Benutzer und Passwort ermitteln
    user_input = form.select_one("input[name*=user], input[name*=login], input[type=text]")
    pass_input = form.select_one("input[type=password]")
    if not user_input or not pass_input:
        raise RuntimeError("Benutzer oder Passwortfeld nicht erkannt")

    payload[user_input.get("name")] = MG_USERNAME
    payload[pass_input.get("name")] = MG_PASSWORD

    # Absenden
    r2 = session.post(login_url, data=payload, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
    r2.raise_for_status()

    # Prüfung auf eingeloggt
    html = r2.text
    if "Logout" not in html and "logout" not in html:
        # Weiter zur Highscore Seite und dort prüfen
        r3 = session.get(HIGHSCORE_URL, headers=SESSION_HEADERS, timeout=30, allow_redirects=True)
        r3.raise_for_status()
        if "Logout" not in r3.text and "logout" not in r3.text:
            # Debug ohne Geheimnisse
            raise RuntimeError("Login fehlgeschlagen. Formular erkannt aber Session nicht authentifiziert")
