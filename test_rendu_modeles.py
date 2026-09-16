"""Bac à sable du rendu des modèles.

Les modèles sont modifiables depuis l'application. Sans bac à sable, l'auteur
d'un modèle obtient une exécution de code arbitraire sur le serveur — qui
héberge aussi la plateforme de recrutement.

Ce test verrouille les deux côtés : les évasions connues sont neutralisées, et
ce dont les modèles ont réellement besoin continue de fonctionner.
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
    from jinja2.sandbox import SandboxedEnvironment

    from app.services.rendu import environnement_modele, rendre_modele

    print("\n— 1. L'environnement est bien un bac à sable —")
    verifier(isinstance(environnement_modele(), SandboxedEnvironment),
             "environnement_modele() renvoie un SandboxedEnvironment")

    print("\n— 2. Les évasions connues ne donnent accès à rien —")
    # Chacune doit soit lever, soit ne rien rendre : jamais l'objet réel.
    evasions = [
        "{{ ''.__class__ }}",
        "{{ ''.__class__.__mro__[1].__subclasses__() }}",
        "{{ ''.__class__.__base__.__subclasses__() }}",
        "{{ cycler.__init__.__globals__.os.popen('id').read() }}",
        "{{ self.__init__.__globals__ }}",
        "{{ ''|attr('__class__') }}",
        "{{ ''|attr('__class__')|attr('__base__')|attr('__subclasses__')() }}",
        "{{ namespace.__init__.__globals__ }}",
        "{{ config }}",
        "{{ ''.__class__.__getattribute__('__subclasses__') }}",
    ]
    for src in evasions:
        court = src[:52]
        try:
            rendu = rendre_modele(src)
            inoffensif = rendu.strip() in ("", "None")
            verifier(inoffensif, f"neutralisé : {court}", f"a rendu « {rendu[:60]} »")
        except Exception:
            # Une exception est une neutralisation parfaitement acceptable
            verifier(True, f"refusé : {court}")

    print("\n— 3. Aucune évasion ne produit un nom de classe Python —")
    for src in evasions:
        try:
            rendu = rendre_modele(src)
        except Exception:
            continue
        for marqueur in ("<class", "<built-in", "subprocess", "os.", "Popen"):
            verifier(marqueur not in rendu,
                     f"« {marqueur} » absent du rendu de {src[:40]}", rendu[:60])

    print("\n— 4. Ce dont les modèles ont besoin fonctionne —")
    cas = [
        ('{{ "{:,.2f}".format(v).replace(",", " ") }}', {"v": 9500.0}, "9 500.00"),
        ('{{ "%.2f"|format(v) }}', {"v": 12.5}, "12.50"),
        ("{% if x %}oui{% else %}non{% endif %}", {"x": True}, "oui"),
        ("{% for n in l %}{{ n }};{% endfor %}", {"l": [1, 2]}, "1;2;"),
        ("{{ nom|upper }}", {"nom": "karim"}, "KARIM"),
        ("{{ a }} — {{ b }}", {"a": "Karim", "b": "CDI"}, "Karim — CDI"),
        ("{{ absent }}", {}, ""),
    ]
    for src, donnees, attendu in cas:
        try:
            rendu = rendre_modele(src, **donnees)
            verifier(attendu in rendu, f"{src[:44]} → « {attendu} »", f"obtenu « {rendu} »")
        except Exception as e:
            verifier(False, f"{src[:44]}", f"{type(e).__name__} : {e}")

    print("\n— 5. Les modèles livrés passent tous le bac à sable —")
    dossier = os.path.join("app", "templates")
    for nom in sorted(os.listdir(dossier)):
        if not nom.endswith(".html"):
            continue
        with open(os.path.join(dossier, nom), encoding="utf-8") as f:
            contenu = f.read()
        try:
            environnement_modele().parse(contenu)
            verifier(True, f"{nom} : syntaxe acceptée")
        except Exception as e:
            verifier(False, f"{nom} : syntaxe acceptée", str(e))

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:5]))
        sys.exit(1)
    print("RESULTAT : BAC A SABLE OK")


if __name__ == "__main__":
    main()
