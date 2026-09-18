"""Le bulletin de paie n'est plus GÉNÉRÉ : il est déposé.

Il est établi par le comptable. L'application en fabriquait un avec des
cotisations calculées de son côté (CNSS 4,48 %, AMO 2,26 %, IR) — deux
bulletins différents pouvaient donc exister pour le même mois, et le bulletin
est un document opposable. La demande sert désormais à le réclamer ; le RH
dépose celui du comptable, ce qui clôt la demande.

Ce test verrouille le refus de génération. Sans lui, un futur ajout de type
pourrait silencieusement remettre l'application en position d'émettre un
bulletin.
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
    from app.services.demande_service import (
        CATEGORIE_DEPOT, DEMANDES_CONFIG, TYPES_SANS_GENERATION, se_genere,
    )

    print("\n— 1. Le bulletin de paie ne se génère pas —")
    verifier(not se_genere("bulletin_paie"), "bulletin_paie → pas de génération")
    verifier("bulletin_paie" in TYPES_SANS_GENERATION,
             "il figure dans TYPES_SANS_GENERATION")

    print("\n— 2. Les autres types continuent de se générer —")
    # Une erreur de périmètre ici priverait le RH de tous ses documents.
    for autre in ("attestation_travail", "attestation_salaire", "ordre_mission",
                  "certificat_presence", "demande_conge"):
        verifier(se_genere(autre), f"{autre} → généré")

    print("\n— 3. Le type reste demandable par le salarié —")
    # Retirer le type de la config aurait supprimé le moyen de RÉCLAMER le
    # bulletin : c'est justement ce qu'on veut garder.
    verifier("bulletin_paie" in DEMANDES_CONFIG,
             "le salarié peut toujours demander son bulletin")
    verifier(CATEGORIE_DEPOT.get("bulletin_paie") == "bulletin_paie",
             "le dépôt le range dans la bonne catégorie")

    print("\n— 4. La génération est refusée, avec un message qui oriente —")
    import inspect

    from app.services import pdf_generator
    # ⚠️ Le refus vit dans `_load_base_data`, pas dans `render_html` : c'est le
    # tronc commun de l'aperçu ET de la génération. Le mettre plus haut aurait
    # laissé l'aperçu afficher un bulletin qu'on refuse ensuite d'émettre.
    source = inspect.getsource(pdf_generator._load_base_data)
    verifier("se_genere" in source,
             "le refus consulte la règle au lieu de la redupliquer")
    verifier("comptable" in source,
             "le message explique d'où vient le document")
    for appelant in (pdf_generator.render_html, pdf_generator.generate_pdf):
        verifier("_load_base_data" in inspect.getsource(appelant),
                 f"{appelant.__name__} passe par le tronc commun")

    print("\n— 5. Le modèle mort est masqué de l'éditeur —")
    # Le laisser dans la liste inviterait à passer du temps sur un document
    # qui ne sort plus jamais.
    from app.routers import templates as routeur_templates
    source = inspect.getsource(routeur_templates.liste_modeles)
    verifier("se_genere" in source, "la liste filtre les types sans génération")

    print("\n— 6. La validation bascule vers le dépôt —")
    from app.routers import rh as routeur_rh
    source = inspect.getsource(routeur_rh.valider_demande)
    verifier("se_genere" in source and "_cloturer_par_depot" in source,
             "valider_demande dévie les types non générés")
    verifier(hasattr(routeur_rh, "deposer_document_demande"),
             "la route de dépôt existe")

    source = inspect.getsource(routeur_rh.deposer_document_demande)
    verifier("read_upload_limited" in source,
             "la taille est plafonnée avant écriture")
    verifier("safe_filename" in source,
             "le nom de fichier est assaini")
    verifier("visible_employe=True" in source,
             "le document déposé est visible par le salarié qui l'a demandé")
    verifier("NextcloudIndisponible" in source,
             "un stockage muet n'écrit pas de ligne orpheline en base")
    verifier("propre demande" in source,
             "un RH ne traite pas sa propre demande")

    print("\n— 7. Le dépôt refuse un type qui, lui, se génère —")
    verifier("se_genere(demande.type)" in source,
             "déposer une attestation renvoie vers « Valider »")

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:4]))
        sys.exit(1)
    print("RESULTAT : BULLETIN PAR DEPOT OK")


if __name__ == "__main__":
    main()
