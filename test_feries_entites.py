"""Fériés par entité (Maroc / France) et jours attendus modifiables."""
import sys
from datetime import date

from app.routers.pointage import _est_exceptionnel, _jours_attendus, _totaux, _weekdays
from app.services.feries import _paques, feries_calcules, feries_mobiles_fr

ok = True


class P:
    def __init__(self, jour, mois=5, categorie="production", type_="normale", valeur=1.0):
        self.date_jour = date(2026, mois, jour)
        self.categorie = categorie
        self.type = type_
        self.valeur = valeur
        self.projet_id = 1 if categorie == "production" else None
        self.commentaire = None


print("— Calendriers distincts —")
ma = {f["date"].isoformat(): f["libelle"] for f in feries_calcules(2026, "MA")}
fr = {f["date"].isoformat(): f["libelle"] for f in feries_calcules(2026, "FR")}
print(f"  Maroc  : {len(ma)} fériés")
print(f"  France : {len(fr)} fériés (dont 3 pascaux)")

# Le 30 juillet (Fête du Trône) est férié au Maroc, pas en France
if "2026-07-30" in ma and "2026-07-30" not in fr:
    print("  OK    Fête du Trône : Maroc uniquement")
else:
    ok = False
    print("  ECHEC Fête du Trône mal rattachée")

# Le 14 juillet est férié en France, pas au Maroc
if "2026-07-14" in fr and "2026-07-14" not in ma:
    print("  OK    Fête Nationale française : France uniquement")
else:
    ok = False
    print("  ECHEC Fête Nationale mal rattachée")

# Le 1er mai est férié dans les deux
if "2026-05-01" in ma and "2026-05-01" in fr:
    print("  OK    Fête du Travail : les deux entités")
else:
    ok = False
    print("  ECHEC Fête du Travail absente d'une entité")

print("\n— Fériés pascaux (calculables, contrairement à l'hégirien) —")
for d, lib in feries_mobiles_fr(2026):
    print(f"  {d} {lib}")
if _paques(2026) == date(2026, 4, 5):
    print("  OK    Pâques 2026 exact")
else:
    ok = False
    print("  ECHEC calcul de Pâques")

print("\n— Jours attendus : fériés déduits automatiquement —")
weekdays = _weekdays(2026, 5)   # mai 2026
feries_mai_ma = {"2026-05-01": "Fête du Travail"}
attendu = _jours_attendus(weekdays, feries_mai_ma, None)
print(f"  jours ouvrés {len(weekdays)} − 1 férié = {attendu:g} attendus")
if attendu == len(weekdays) - 1:
    print("  OK    le férié est retiré des jours attendus")
else:
    ok = False
    print(f"  ECHEC attendu = {attendu}")

print("\n— Jours attendus imposés par l'administrateur (démission, temps partiel) —")
impose = _jours_attendus(weekdays, feries_mai_ma, 8)
if impose == 8:
    print("  OK    8 j imposés priment sur le calcul")
else:
    ok = False
    print(f"  ECHEC {impose}")

print("\n— Salarié parti en cours de mois : complétion sous 100 % —")
t = _totaux([P(4), P(5), P(6), P(7), P(8)], weekdays, feries_mai_ma)
print(f"  réalisé {t['realise']:g} / attendu {t['attendu']:g} → {t['completion']} % (écart {t['ecart']:g})")
if t["completion"] < 100 and t["ecart"] < 0:
    print("  OK    sous-complétion possible, non bloquante")
else:
    ok = False
    print("  ECHEC la sous-complétion devrait être permise")

print("\n— Travail un jour férié : compté et signalé —")
# 1er mai 2026 = vendredi férié. Le salarié pointe son PROJET ce jour-là.
t2 = _totaux([P(1)], weekdays, feries_mai_ma)
if _est_exceptionnel(date(2026, 5, 1), feries_mai_ma) and t2["exceptionnel"] == 1:
    print("  OK    le jour férié travaillé est isolé comme majorable")
else:
    ok = False
    print(f"  ECHEC exceptionnel = {t2['exceptionnel']}")

print("\nRESULTAT :", "ENTITES ET JOURS ATTENDUS OK" if ok else "DES CONTROLES ONT ECHOUE")
sys.exit(0 if ok else 1)
