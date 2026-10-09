"""Jednorazowe INTERAKTYWNE logowanie do Power BI na maszynie Z PRZEGLĄDARKĄ.

Po co: serwer GROOVE stoi w chmurze bez przeglądarki, a polityka Azure (Conditional
Access) blokuje logowanie device-code. Ten sam popup, którego używa STOCK_PANDAS_BI,
CA przepuszcza. Tu logujesz się raz w przeglądarce na swoim PC, a wynik (cache MSAL z
refresh tokenem) przenosisz na serwer:

    pip install msal
    python tools/powerbi_login_local.py --tenant example.com --out cache.json
    # potem na serwerze (Coolify → Terminal):
    python manage.py powerbi_import_cache < cache.json

Serwer odświeża potem token po cichu (acquire_token_silent) — bez przeglądarki.
UWAGA: cache.json zawiera refresh token — traktuj jak sekret, skasuj po wgraniu.
"""
import argparse
import sys

# Publiczny klient Microsoftu (Azure CLI) — ma zarejestrowany redirect http://localhost,
# więc popup działa bez rejestracji własnej aplikacji. Ten sam, którego używa GROOVE.
CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]


def main():
    ap = argparse.ArgumentParser(description="Interaktywne logowanie do Power BI → cache MSAL.")
    ap.add_argument("--tenant", default="example.com",
                    help="Dzierżawa (domena), np. example.com. Musi zgadzać się z GROOVE "
                         "(POWERBI_TENANT_ID).")
    ap.add_argument("--out", default="cache.json", help="Plik wyjściowy z cache MSAL.")
    a = ap.parse_args()

    import msal
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{a.tenant}",
        token_cache=cache)

    result = app.acquire_token_interactive(scopes=SCOPES)   # otwiera przeglądarkę
    if "access_token" not in result:
        print("Logowanie nieudane:", result.get("error_description") or result, file=sys.stderr)
        sys.exit(1)

    with open(a.out, "w", encoding="utf-8") as f:
        f.write(cache.serialize())
    accts = app.get_accounts()
    who = accts[0].get("username") if accts else "(konto)"
    print(f"OK — zalogowano jako {who}. Cache zapisany do: {a.out}")
    print(f"Teraz na serwerze: python manage.py powerbi_import_cache < {a.out}")


if __name__ == "__main__":
    main()
