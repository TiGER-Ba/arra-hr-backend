"""Nombre d'enfants lié à la situation familiale.

Règle : il n'est saisi que pour un salarié « Marié(e) ». Pour toute autre
situation le champ disparaît du formulaire — la valeur doit alors être remise à
NULL en base, faute de quoi un ancien nombre resterait stocké sans être visible
et ressortirait dans les documents générés.

0 enfant est une réponse valide, et doit rester distinct de « non renseigné ».
"""
import os
import sys

os.environ.setdefault("ENVIRONMENT", "development")

ECHECS = []


def verifier(condition, libelle, detail=""):
    if condition:
        print(f"  OK    {libelle}")
    else:
        print(f"  ECHEC {libelle}" + (f" — {detail}" if detail else ""))
        ECHECS.append(libelle)


def main():
    from fastapi import HTTPException

    from app.routers.users import MAX_ENFANTS, SITUATION_AVEC_ENFANTS, _resoudre_enfants

    def refuse(situation, nombre):
        """Renvoie le message d'erreur, ou None si la valeur est acceptée."""
        try:
            _resoudre_enfants(situation, nombre)
            return None
        except HTTPException as e:
            return e.detail

    print("\n— 1. Marié : la valeur est exigée —")
    verifier(refuse("Marié(e)", None) is not None, "marié sans nombre → refusé")
    verifier(refuse("Marié(e)", "") is not None, "marié, champ vide → refusé")
    msg = refuse("Marié(e)", None)
    verifier(msg and "marié" in msg.lower(), "le refus dit pourquoi", msg or "")

    print("\n— 2. Marié : les valeurs plausibles passent —")
    verifier(_resoudre_enfants("Marié(e)", 0) == 0,
             "0 enfant accepté (≠ non renseigné)", str(_resoudre_enfants("Marié(e)", 0)))
    verifier(_resoudre_enfants("Marié(e)", 3) == 3, "3 enfants")
    verifier(_resoudre_enfants("Marié(e)", "2") == 2, "chaîne « 2 » convertie en entier")
    verifier(_resoudre_enfants("Marié(e)", MAX_ENFANTS) == MAX_ENFANTS,
             f"borne haute ({MAX_ENFANTS}) acceptée")

    print("\n— 3. Marié : les valeurs aberrantes sont refusées —")
    verifier(refuse("Marié(e)", -1) is not None, "nombre négatif refusé")
    verifier(refuse("Marié(e)", MAX_ENFANTS + 1) is not None,
             f"au-delà de {MAX_ENFANTS} refusé")
    verifier(refuse("Marié(e)", "beaucoup") is not None, "texte non numérique refusé")

    print("\n— 4. Toute autre situation : remise à NULL —")
    for situation in ("Célibataire", "Divorcé(e)", "Veuf(ve)", None, ""):
        libelle = situation or "(vide)"
        verifier(_resoudre_enfants(situation, 4) is None,
                 f"{libelle} → NULL même si 4 est transmis",
                 str(_resoudre_enfants(situation, 4)))
    verifier(_resoudre_enfants("Célibataire", None) is None,
             "célibataire sans valeur → NULL, et aucun refus")

    print("\n— 5. La constante partagée correspond au libellé du formulaire —")
    from app.routers.users import VALID_SITUATIONS
    verifier(SITUATION_AVEC_ENFANTS in VALID_SITUATIONS,
             "« Marié(e) » fait bien partie des situations valides",
             SITUATION_AVEC_ENFANTS)

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS))
        sys.exit(1)
    print("RESULTAT : SITUATION FAMILIALE OK")


if __name__ == "__main__":
    main()
