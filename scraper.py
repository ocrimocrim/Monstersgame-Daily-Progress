import os
import requests

MG_SESSIONID = os.getenv("MG_SESSIONID", "").strip()
MG_USERNAME = os.getenv("MG_USERNAME", "").strip()
MG_PASSWORD = os.getenv("MG_PASSWORD", "").strip()
MG_HIGHSCORE_URL = os.getenv("MG_HIGHSCORE_URL", "").strip()
MG_CSRFTOKEN = os.getenv("MG_CSRFTOKEN", "").strip()
MG_CONNECT_ID = os.getenv("MG_CONNECT_ID", "").strip()

session = requests.Session()

def login_with_sessionid():
    if not MG_SESSIONID:
        raise ValueError("Kein MG_SESSIONID gesetzt")

    # Session-Cookie sauber setzen
    session.cookies.set("sessionid", MG_SESSIONID, domain="moonid.net")
    r = session.get(MG_HIGHSCORE_URL)
    if "Login" in r.text or "Passwort" in r.text:
        print("Session ungültig → versuche Login mit Username/Passwort...")
        return login_with_username_password()
    return r.text

def login_with_username_password():
    if not MG_USERNAME or not MG_PASSWORD:
        raise ValueError("Weder gültige Session noch Username/Passwort angegeben")

    payload = {
        "username": MG_USERNAME,
        "password": MG_PASSWORD,
    }
    r = session.post("https://int3.monstersgame.moonid.net/index.php?ac=login", data=payload)
    if "Login" in r.text or "Passwort" in r.text:
        raise ValueError("Login fehlgeschlagen. Prüfe Username/Passwort.")
    return session.get(MG_HIGHSCORE_URL).text

def main():
    try:
        html = login_with_sessionid()
    except Exception as e:
        print(f"Fehler mit SessionID: {e}")
        print("Versuche Login mit Username/Passwort...")
        html = login_with_username_password()

    # hier kannst du weitermachen mit HTML verarbeiten
    print("Highscore-Seite erfolgreich geladen.")

if __name__ == "__main__":
    main()
