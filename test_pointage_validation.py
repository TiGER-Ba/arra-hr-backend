"""Validation d'une feuille de temps : le corps de requête doit être explicite.

⚠️ **Le bug corrigé le 21/09/2026.** La route `POST /pointage/valider`
déclarait son corps comme `RejetBody | PeriodeBody | dict`. Pydantic essaie les
membres d'une union dans l'ordre et retient le premier qui valide :

    {"employe_id": 12, "annee": 2026, "mois": 9}
      → RejetBody   : échoue (pas de « motif »)
      → PeriodeBody : RÉUSSIT — mais ce modèle n'a pas de champ `employe_id`,
                      qui est donc SILENCIEUSEMENT SUPPRIMÉ

La route répondait alors « employe_id requis » alors que le client l'avait bien
envoyé : un message qui accuse l'appelant d'une faute commise par le serveur.
Valider une feuille de temps était tout simplement impossible.

Ce test reproduit le mécanisme au lieu de se contenter de vérifier le résultat :
il empêche qu'une union soit réintroduite sur cette route.
"""
import inspect
import os
import sys
import typing

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_pointage_validation.db")

ECHECS = []


def verifier(condition, libelle, detail=""):
    if condition:
        print(f"  OK    {libelle}")
    else:
        print(f"  ECHEC {libelle}" + (f" — {detail}" if detail else ""))
        ECHECS.append(libelle)


def main():
    from pydantic import TypeAdapter, ValidationError

    from app.routers import pointage as p

    CORPS = {"employe_id": 12, "annee": 2026, "mois": 9}

    print("\n— 1. Le modèle de validation garde bien employe_id —")
    corps = p.ValidationBody(**CORPS)
    verifier(corps.employe_id == 12, "employe_id conservé", str(corps.employe_id))
    verifier(corps.annee == 2026 and corps.mois == 9, "année et mois conservés")

    print("\n— 2. Un champ manquant est refusé, et NOMMÉ —")
    # Le client doit savoir QUOI corriger. Un 400 générique ne le dit pas.
    for absent in ("employe_id", "annee", "mois"):
        partiel = {k: v for k, v in CORPS.items() if k != absent}
        try:
            p.ValidationBody(**partiel)
            verifier(False, f"sans {absent} : refusé", "accepté à tort")
        except ValidationError as e:
            verifier(absent in str(e), f"sans {absent} : refusé en le nommant")

    print("\n— 3. ⚠️ La route n'utilise PAS d'union pour son corps —")
    # C'est la cause racine. Une union réintroduite ferait resurgir le bug.
    annotation = inspect.signature(p.valider_feuille).parameters["payload"].annotation
    est_union = typing.get_origin(annotation) is typing.Union or "|" in str(annotation)
    verifier(not est_union, "le corps est un modèle unique", str(annotation))
    verifier(annotation is p.ValidationBody,
             "et c'est bien ValidationBody", str(annotation))

    print("\n— 4. Reproduction du mécanisme : l'union PERDAIT le champ —")
    # On reconstitue l'ancienne annotation pour montrer que le test 3 n'est pas
    # une précaution abstraite.
    ancienne = TypeAdapter(p.RejetBody | p.PeriodeBody | dict).validate_python(CORPS)
    verifier(getattr(ancienne, "employe_id", None) is None,
             "l'ancienne union rendait bien un PeriodeBody sans employe_id",
             type(ancienne).__name__)
    nouvelle = TypeAdapter(p.ValidationBody).validate_python(CORPS)
    verifier(nouvelle.employe_id == 12,
             "la nouvelle annotation le conserve", str(nouvelle.employe_id))

    print("\n— 5. Le rejet, lui, exige toujours un motif —")
    # Rejeter sans dire pourquoi laisserait le salarié sans recours.
    try:
        p.RejetBody(**CORPS)
        verifier(False, "rejet sans motif : refusé", "accepté à tort")
    except ValidationError as e:
        verifier("motif" in str(e), "rejet sans motif : refusé en nommant « motif »")
    verifier(p.RejetBody(**CORPS, motif="feuille incomplète").employe_id == 12,
             "un rejet complet garde employe_id")

    print("\n— 6. Aucune autre route ne déclare un corps en union —")
    from fastapi.routing import APIRoute

    import app.main

    suspectes = []
    for route in app.main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for nom, param in inspect.signature(route.endpoint).parameters.items():
            a = param.annotation
            if typing.get_origin(a) is not typing.Union:
                continue
            membres = [m for m in typing.get_args(a) if m is not type(None)]
            # `X | None` est un simple optionnel : aucun risque de bascule.
            if len(membres) > 1:
                suspectes.append(f"{route.path} · {nom}: {a}")
    verifier(not suspectes, "aucune union à plusieurs modèles", str(suspectes))

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:4]))
        sys.exit(1)
    print("RESULTAT : VALIDATION POINTAGE OK")


if __name__ == "__main__":
    main()
