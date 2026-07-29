"""Vérifie que la chaîne d'authentification fonctionne après la montée de version
de python-jose (3.3.0 → 3.5.0) : hachage, jeton valide, jeton falsifié, expiration.
"""
from datetime import timedelta

from jose import jwt

from app.config import settings
from app.services.auth import create_access_token, get_password_hash, verify_password

ok = True

print("— Hachage du mot de passe (bcrypt) —")
h = get_password_hash("motdepasse123")
if verify_password("motdepasse123", h) and not verify_password("mauvais", h):
    print("  OK    vérification correcte")
else:
    ok = False
    print("  ECHEC")

print("\n— Émission / lecture d'un jeton —")
token = create_access_token({"sub": "42", "role": "admin"})
payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
if payload.get("sub") == "42" and payload.get("role") == "admin":
    print("  OK    aller-retour du jeton")
else:
    ok = False
    print(f"  ECHEC payload={payload}")

print("\n— Jeton signé avec une autre clé (falsification) —")
faux = jwt.encode({"sub": "1", "role": "admin"}, "clef-de-lattaquant-quelconque", algorithm="HS256")
try:
    jwt.decode(faux, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    ok = False
    print("  ECHEC : un jeton falsifié a été accepté")
except Exception:
    print("  OK    jeton falsifié rejeté")

print("\n— Jeton expiré —")
expire = create_access_token({"sub": "42"}, expires_delta=timedelta(seconds=-10))
try:
    jwt.decode(expire, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    ok = False
    print("  ECHEC : un jeton expiré a été accepté")
except Exception:
    print("  OK    jeton expiré rejeté")

print("\n— Algorithme 'none' (attaque classique) —")
try:
    nonalg = jwt.encode({"sub": "1", "role": "admin"}, "", algorithm="none")
    jwt.decode(nonalg, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    ok = False
    print("  ECHEC : algorithme 'none' accepté")
except Exception:
    print("  OK    algorithme 'none' rejeté")

print("\nRESULTAT :", "AUTHENTIFICATION OK" if ok else "DES CONTROLES ONT ECHOUE")
raise SystemExit(0 if ok else 1)
