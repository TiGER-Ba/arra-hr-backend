"""Cycle de vie d'une fiche : brouillon → actif → sortie.

Vérifie les règles qui font la différence entre une donnée juste et une donnée
qui traîne : une sortie sans date ni motif est refusée, un motif de salarié est
refusé à un externe, et surtout **revenir en arrière efface** la date de fin —
sinon la base garderait une sortie invisible à la saisie mais toujours reprise
par les documents générés.
"""
import os
import sys
from datetime import date

os.environ.setdefault("ENVIRONMENT", "development")

ECHECS = []


def verifier(condition, libelle, detail=""):
    if condition:
        print(f"  OK    {libelle}")
    else:
        print(f"  ECHEC {libelle}" + (f" — {detail}" if detail else ""))
        ECHECS.append(libelle)


def refuse(fn, libelle, attendu=""):
    from fastapi import HTTPException
    try:
        fn()
        verifier(False, libelle, "aucune erreur levée")
    except HTTPException as e:
        ok = attendu.lower() in str(e.detail).lower() if attendu else True
        verifier(ok, libelle, str(e.detail))


def main():
    from app.routers.users import _resoudre_statut
    from app.services import statuts as S

    print("\n— 1. Le cycle de vie est complet et ordonné —")
    verifier(list(S.STATUTS) == ["brouillon", "actif_interne", "actif_production",
                                 "desistement", "quitte"],
             "cinq statuts, dans l'ordre du cycle", str(list(S.STATUTS)))
    verifier(S.est_actif("actif_interne") and S.est_actif("actif_production"),
             "les deux « actifs » comptent dans l'effectif")
    verifier(not S.est_actif("brouillon"), "un brouillon n'est pas un effectif")

    print("\n— 2. Un dossier clos coupe l'accès, les autres non —")
    for statut in ("desistement", "quitte"):
        verifier(not S.compte_doit_etre_actif(statut), f"{statut} → compte désactivé")
    for statut in ("brouillon", "actif_interne", "actif_production"):
        verifier(S.compte_doit_etre_actif(statut), f"{statut} → compte ouvert")

    print("\n— 3. Seul le brouillon échappe aux champs obligatoires —")
    verifier(not S.exige_fiche_complete("brouillon"), "brouillon : fiche incomplète tolérée")
    for statut in ("actif_interne", "actif_production", "desistement", "quitte"):
        verifier(S.exige_fiche_complete(statut), f"{statut} : fiche complète exigée")

    print("\n— 4. Les motifs de fin suivent la nature du contrat —")
    verifier("Démission" in S.motifs_fin("CDI"), "un salarié peut démissionner")
    verifier("Démission" not in S.motifs_fin("Freelance"),
             "un freelance ne démissionne pas", str(S.motifs_fin("Freelance")))
    verifier("Fin de mission" in S.motifs_fin("Prestataire"),
             "un prestataire termine une mission")
    verifier("Fin de contrat à terme" in S.motifs_fin("CDD"),
             "un CDD arrivé à terme n'est pas une rupture")

    print("\n— 5. Une sortie sans date ni motif est refusée —")
    refuse(lambda: _resoudre_statut("quitte", "CDI", None, "Démission", None),
           "sortie sans date : refusée", "date de fin")
    refuse(lambda: _resoudre_statut("quitte", "CDI", date(2026, 6, 30), None, None),
           "sortie sans motif : refusée", "motif")
    refuse(lambda: _resoudre_statut("quitte", "Freelance", date(2026, 6, 30), "Licenciement", None),
           "motif de salarié refusé à un externe", "invalide")
    refuse(lambda: _resoudre_statut(
               "quitte", "CDI", date(2020, 1, 1), "Démission", None, date(2024, 1, 1)),
           "date de fin antérieure au début : refusée", "précéder")

    print("\n— 6. Un désistement exige son explication —")
    refuse(lambda: _resoudre_statut("desistement", "CDI", None, None, "   "),
           "désistement sans commentaire : refusé", "commentaire")
    r = _resoudre_statut("desistement", "CDI", None, None, "A accepté une autre offre")
    verifier(r["commentaire_statut"] == "A accepté une autre offre",
             "le motif du renoncement est conservé")

    print("\n— 7. Ce qui ne s'applique plus est EFFACÉ —")
    # Le cas qui corrompt les documents : une sortie annulée dont la date reste.
    r = _resoudre_statut("actif_production", "CDI", date(2026, 6, 30), "Démission",
                         "réintégré")
    verifier(r["statut"] == "actif_production", "le retour en actif est accepté")
    verifier(r["date_fin"] is None, "la date de fin est effacée", str(r["date_fin"]))
    verifier(r["motif_fin"] is None, "le motif est effacé", str(r["motif_fin"]))
    verifier(r["commentaire_statut"] == "réintégré", "le commentaire, lui, reste")

    r = _resoudre_statut("brouillon", "Freelance", date(2026, 1, 1), "Fin de mission", None)
    verifier(r["date_fin"] is None and r["motif_fin"] is None,
             "un brouillon ne porte ni date ni motif de sortie")

    print("\n— 8. Valeurs par défaut et rejets —")
    verifier(_resoudre_statut(None, "CDI", None, None, None)["statut"] == "actif_interne",
             "statut absent → actif interne")
    refuse(lambda: _resoudre_statut("licencie", "CDI", None, None, None),
           "statut inventé : refusé", "invalide")

    print("\n— 9. Une sortie valide passe intégralement —")
    r = _resoudre_statut("quitte", "CDD", date(2026, 6, 30), "Fin de contrat à terme",
                         "Non renouvelé", date(2026, 1, 1))
    verifier(r["statut"] == "quitte" and r["date_fin"] == date(2026, 6, 30)
             and r["motif_fin"] == "Fin de contrat à terme"
             and r["commentaire_statut"] == "Non renouvelé",
             "date, motif et commentaire enregistrés ensemble", str(r))

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:4]))
        sys.exit(1)
    print("RESULTAT : STATUTS OK")


if __name__ == "__main__":
    main()
