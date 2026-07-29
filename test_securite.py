"""Vérification des garde-fous de sécurité (exécutable : python test_securite.py)."""
from app.services.security import safe_filename, safe_join, verifier_configuration

ok = True

print("— Assainissement des noms de fichiers (traversée de répertoire) —")
for c in ["../../../etc/passwd", "..\\..\\windows\\system32\\evil.txt", "/etc/shadow",
          "C:\\Windows\\x.png", "....//x.txt", "normal file (1).pdf", "", ".htaccess"]:
    got = safe_filename(c)
    dangereux = ("/" in got) or ("\\" in got) or got.startswith(".") or got == ""
    if dangereux:
        ok = False
        print(f"  ECHEC {c!r} -> {got!r}")
    else:
        print(f"  OK    {c!r:45} -> {got!r}")

print("\n— safe_join —")
try:
    safe_join("uploads/depot", "../../app/main.py")
    ok = False
    print("  ECHEC : la traversée est passée")
except Exception:
    print("  OK    traversée bloquée")

print("\n— Configuration de production —")


class S:
    ENVIRONMENT = "production"
    SECRET_KEY = "changeme_at_least_32_chars_long_secret"
    DATABASE_URL = "postgresql://x"


try:
    verifier_configuration(S())
    ok = False
    print("  ECHEC : SECRET_KEY par défaut accepté en production")
except RuntimeError:
    print("  OK    démarrage refusé (SECRET_KEY par défaut)")


class S2:
    ENVIRONMENT = "production"
    SECRET_KEY = "x" * 40
    DATABASE_URL = "postgresql://x"


import os

os.environ["ALLOWED_ORIGINS"] = "https://admin.arra-engineering.com"
try:
    verifier_configuration(S2())
    print("  OK    configuration de production valide acceptée")
except RuntimeError as e:
    ok = False
    print(f"  ECHEC : config valide refusée -> {e}")

print("\nRESULTAT :", "TOUS LES GARDE-FOUS OK" if ok else "DES CONTROLES ONT ECHOUE")
raise SystemExit(0 if ok else 1)
