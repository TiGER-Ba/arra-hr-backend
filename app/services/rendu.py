"""Rendu des modèles de documents, sous bac à sable.

⚠️ Les modèles sont **modifiables depuis l'application** (Paramétrage › Modèles).
Un `jinja2.Environment` ordinaire donne alors à l'auteur du modèle un accès
Python arbitraire : `{{ ''.__class__.__mro__[1].__subclasses__() }}` suffit à
sortir du rendu et à exécuter du code sur le serveur.

Ce serveur héberge aussi la plateforme de recrutement : une prise de contrôle
depuis l'espace RH franchirait l'isolation entre les deux. Le bac à sable de
Jinja bloque les attributs internes (`__class__`, `__globals__`…) et les
méthodes dangereuses, tout en laissant passer ce dont les modèles ont besoin —
`{{ ... }}`, `{% if %}`, `|format`, `.replace()`.

Toujours passer par `environnement_modele()` pour rendre un contenu venant de
la base. `Environment()` nu est réservé aux gabarits livrés avec le code.
"""
from jinja2.sandbox import SandboxedEnvironment


def environnement_modele() -> SandboxedEnvironment:
    """Environnement Jinja restreint, pour tout modèle éditable par l'utilisateur."""
    return SandboxedEnvironment(autoescape=False)


def rendre_modele(contenu_html: str, **donnees) -> str:
    """Rend un modèle éditable avec ses données."""
    return environnement_modele().from_string(contenu_html).render(**donnees)
