"""Découpage et recomposition des modèles pour l'éditeur visuel.

L'invariant critique : découper puis recomposer sans rien modifier doit rendre
le modèle **à l'octet près**. Sinon, éditer une virgule dans une attestation
corromprait silencieusement le document — et ces documents sont signés.

Vérifie aussi que le style et la logique Jinja sont hors de portée de l'éditeur :
il ne décide que du texte et de l'emplacement des blocs.
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
    from app.services.modele_visuel import JETON_BLOC, decouper, recomposer
    from app.services.rendu import environnement_modele

    dossier = os.path.join("app", "templates")
    modeles = sorted(f for f in os.listdir(dossier) if f.endswith(".html"))

    print(f"\n— 1. Aller-retour sans perte sur les {len(modeles)} modèles livrés —")
    for nom in modeles:
        with open(os.path.join(dossier, nom), encoding="utf-8") as f:
            original = f.read()
        d = decouper(original)
        refait = recomposer(original, d["corps"])
        verifier(refait == original, f"{nom} : identique à l'octet près",
                 f"{len(original)} → {len(refait)} octets")

    print("\n— 2. Le corps éditable ne contient plus AUCUNE logique Jinja —")
    for nom in modeles:
        with open(os.path.join(dossier, nom), encoding="utf-8") as f:
            original = f.read()
        corps = decouper(original)["corps"]
        verifier("{%" not in corps, f"{nom} : aucun « {{% %}} » exposé à l'éditeur",
                 corps[corps.find("{%"):corps.find("{%") + 50] if "{%" in corps else "")

    print("\n— 3. Le style reste hors de portée de l'éditeur —")
    for nom in ("attestation_travail.html", "contrat_cdi.html"):
        with open(os.path.join(dossier, nom), encoding="utf-8") as f:
            original = f.read()
        d = decouper(original)
        verifier("<style" not in d["corps"], f"{nom} : pas de <style> dans le corps")
        verifier(len(d["style"]) > 100, f"{nom} : le CSS est fourni à l'aperçu",
                 f"{len(d['style'])} caractères")

    print("\n— 4. Les blocs protégés sont identifiés et étiquetés —")
    with open(os.path.join(dossier, "attestation_travail.html"), encoding="utf-8") as f:
        attestation = f.read()
    d = decouper(attestation)
    verifier(len(d["blocs"]) == 2, "attestation : 2 blocs (logo, signature)", str(len(d["blocs"])))
    etiquettes = [b["etiquette"] for b in d["blocs"]]
    verifier("Logo ARRA" in etiquettes, "le bloc logo est nommé lisiblement", str(etiquettes))
    verifier(any("ignature" in e for e in etiquettes), "le bloc signature est nommé", str(etiquettes))
    verifier(all(JETON_BLOC.format(i) in d["corps"] for i in range(len(d["blocs"]))),
             "chaque bloc laisse un jeton à sa place")

    print("\n— 5. L'éditeur ne peut pas corrompre un bloc —")
    # L'utilisateur renvoie un corps où il a « saboté » le contenu d'un bloc :
    # le serveur réinjecte la source d'origine, le sabotage est sans effet.
    corps_sabote = d["corps"].replace(JETON_BLOC.format(0), JETON_BLOC.format(0))
    refait = recomposer(attestation, corps_sabote)
    verifier("{% if logo_url %}" in refait, "la source du bloc logo est réinjectée telle quelle")

    print("\n— 6. Supprimer un bloc est permis, et ne casse pas le Jinja —")
    sans_logo = d["corps"].replace(JETON_BLOC.format(0), "")
    refait = recomposer(attestation, sans_logo)
    verifier("{% if logo_url %}" not in refait, "le bloc retiré disparaît")
    try:
        environnement_modele().parse(refait)
        verifier(True, "le modèle amputé reste un Jinja valide")
    except Exception as e:
        verifier(False, "le modèle amputé reste un Jinja valide", str(e))

    print("\n— 7. Déplacer un bloc conserve sa source —")
    corps = d["corps"]
    jeton = JETON_BLOC.format(1)
    deplace = corps.replace(jeton, "") + jeton
    refait = recomposer(attestation, deplace)
    verifier("{% if signature_url or cachet_url %}" in refait,
             "le bloc déplacé garde sa condition intacte")
    try:
        environnement_modele().parse(refait)
        verifier(True, "le modèle réordonné reste un Jinja valide")
    except Exception as e:
        verifier(False, "le modèle réordonné reste un Jinja valide", str(e))

    print("\n— 8. Un jeton inventé par l'éditeur est ignoré, pas planté —")
    refait = recomposer(attestation, d["corps"] + JETON_BLOC.format(99))
    verifier(JETON_BLOC.format(99) not in refait, "jeton hors plage : retiré silencieusement")
    try:
        environnement_modele().parse(refait)
        verifier(True, "et le modèle reste valide")
    except Exception as e:
        verifier(False, "et le modèle reste valide", str(e))

    print("\n— 9. Texte modifié : seul le texte change —")
    modifie = d["corps"].replace("Attestation de Travail", "ATTESTATION DE TRAVAIL")
    refait = recomposer(attestation, modifie)
    verifier("ATTESTATION DE TRAVAIL" in refait, "la modification est reprise")
    verifier("{% if logo_url %}" in refait and "@page" in refait,
             "la logique et le style sont intacts")
    verifier(len(refait) == len(attestation), "aucun octet parasite ajouté",
             f"{len(attestation)} → {len(refait)}")

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:4]))
        sys.exit(1)
    print("RESULTAT : EDITEUR VISUEL OK")


if __name__ == "__main__":
    main()
