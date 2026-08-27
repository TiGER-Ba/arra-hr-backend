"""Vérifie le traitement du travail hors jours ouvrés (week-end / férié).

Enjeu : au Maroc, travailler un jour de repos ouvre droit à majoration. Ces
jours doivent donc être comptés SÉPARÉMENT de la production ordinaire et
remonter jusqu'à la paie — sinon la fonctionnalité n'a aucune valeur.
"""
import sys
from datetime import date

from app.routers.pointage import _est_exceptionnel, _totaux, _weekdays

ok = True


class P:
    def __init__(self, jour, categorie="production", type_="normale", valeur=1.0, com=None):
        self.date_jour = date(2026, 8, jour)
        self.categorie = categorie
        self.type = type_
        self.valeur = valeur
        self.projet_id = 1 if categorie == "production" else None
        self.commentaire = com


# Août 2026 : le 1er est un samedi, le 2 un dimanche.
weekdays = _weekdays(2026, 8)
feries = {"2026-08-14": "Allégeance Oued Eddahab", "2026-08-20": "Révolution du Roi et du Peuple"}

print("— Détection des jours de repos —")
cas = [
    (date(2026, 8, 1), True, "samedi"),
    (date(2026, 8, 2), True, "dimanche"),
    (date(2026, 8, 3), False, "lundi ordinaire"),
    (date(2026, 8, 14), True, "jour férié (un vendredi)"),
]
for jour, attendu, libelle in cas:
    obtenu = _est_exceptionnel(jour, feries)
    if obtenu == attendu:
        print(f"  OK    {libelle} → exceptionnel={obtenu}")
    else:
        ok = False
        print(f"  ECHEC {libelle} → {obtenu}, attendu {attendu}")

print("\n— Comptage : 18 j ouvrés + 1 samedi + 1 férié travaillés —")
saisies = [P(j) for j in (3, 4, 5, 6, 7, 10, 11, 12, 13, 17, 18, 19, 21, 24, 25, 26, 27, 28)]
saisies.append(P(1, com="Livraison client urgente"))   # samedi
saisies.append(P(14, com="Astreinte"))                 # férié

t = _totaux(saisies, weekdays, feries)
print(f"  jours ouvrés   : {t['jours_ouvres']:g}")
print(f"  production     : {t['production']:g}")
print(f"  exceptionnel   : {t['exceptionnel']:g}")
print(f"  complétion     : {t['completion']} %")

if t["exceptionnel"] == 2:
    print("  OK    les 2 jours de repos travaillés sont isolés")
else:
    ok = False
    print(f"  ECHEC exceptionnel = {t['exceptionnel']} (attendu 2)")

if t["production"] == 20:
    print("  OK    ils comptent aussi dans la production (18 + 2)")
else:
    ok = False
    print(f"  ECHEC production = {t['production']} (attendu 20)")

print("\n— Effet du samedi sur le réalisé —")
# Comparaison à scénario identique, samedi retiré : c'est la seule façon de
# mesurer l'apport réel du jour de repos (le total dépend aussi des fériés).
sans_samedi = [p for p in saisies if p.date_jour.day != 1]
t_sans = _totaux(sans_samedi, weekdays, feries)
if t["realise"] == t_sans["realise"] + 1:
    print(f"  OK    le samedi ajoute 1 j au réalisé ({t_sans['realise']:g} → {t['realise']:g})")
else:
    ok = False
    print(f"  ECHEC réalisé {t_sans['realise']} → {t['realise']}")

if t["exceptionnel"] == t_sans["exceptionnel"] + 1:
    print(f"  OK    et 1 j majorable de plus ({t_sans['exceptionnel']:g} → {t['exceptionnel']:g})")
else:
    ok = False
    print(f"  ECHEC majorable {t_sans['exceptionnel']} → {t['exceptionnel']}")

print("\n— Une demi-journée de samedi compte 0,5 —")
t2 = _totaux([P(1, valeur=0.5)], weekdays, feries)
if t2["exceptionnel"] == 0.5:
    print("  OK    0,5 j majorable")
else:
    ok = False
    print(f"  ECHEC {t2['exceptionnel']}")

print("\n— Un congé ne doit pas être compté comme majorable —")
t3 = _totaux([P(3, categorie="absence", type_="conge_paye")], weekdays, feries)
if t3["exceptionnel"] == 0 and t3["conge_paye"] == 1:
    print("  OK    congé ordinaire : 0 j majorable")
else:
    ok = False
    print(f"  ECHEC {t3}")

print("\nRESULTAT :", "TRAVAIL EXCEPTIONNEL CORRECTEMENT TRAITE" if ok else "DES CONTROLES ONT ECHOUE")
sys.exit(0 if ok else 1)
