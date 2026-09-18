"""Devise d'un salarié, déduite de son entité de rattachement.

ARRA Maroc paie en dirhams, ARRA France en euros. La devise n'est donc pas une
donnée à saisir : elle découle de `Employe.entite`, au même titre que le
calendrier des jours fériés (voir `services/feries.py`).

⚠️ Ne JAMAIS additionner des montants d'entités différentes : un total
« 250 000 » mêlant MAD et EUR n'a aucun sens. Les agrégats (masse salariale…)
doivent être ventilés par entité — voir `totaliser_par_entite()`.
"""
from collections import defaultdict

DEVISES: dict[str, dict[str, str]] = {
    "MA": {"code": "MAD", "symbole": "MAD", "libelle": "Dirham marocain", "locale": "fr-MA"},
    "FR": {"code": "EUR", "symbole": "€", "libelle": "Euro", "locale": "fr-FR"},
}

DEVISE_DEFAUT = "MAD"


def devise_entite(entite: str | None) -> str:
    """Code ISO de la devise d'une entité (« MAD » ou « EUR »)."""
    return DEVISES.get((entite or "").upper(), {}).get("code", DEVISE_DEFAUT)


def devise_employe(employe) -> str:
    """Devise du salarié, d'après son entité de rattachement."""
    return devise_entite(getattr(employe, "entite", None))


def symbole(code_devise: str) -> str:
    """Symbole d'affichage : « € » pour l'euro, « MAD » pour le dirham."""
    for d in DEVISES.values():
        if d["code"] == code_devise:
            return d["symbole"]
    return code_devise


def formater_montant(valeur: float | int | None, entite: str | None = None,
                     code_devise: str | None = None) -> str:
    """« 9 500,00 MAD » ou « 3 200,00 € », espace insécable fine en séparateur."""
    if valeur is None:
        return "—"
    code = code_devise or devise_entite(entite)
    montant = f"{float(valeur):,.2f}".replace(",", " ").replace(".", ",")
    return f"{montant} {symbole(code)}"


def totaliser_par_entite(employes, valeur=lambda e: float(e.salaire_base)) -> list[dict]:
    """Somme d'un montant par entité, chacune avec sa devise.

    Renvoie une liste ordonnée (Maroc puis France) plutôt qu'un total unique :
    additionner des dirhams et des euros donnerait un nombre faux.
    """
    totaux: dict[str, float] = defaultdict(float)
    effectifs: dict[str, int] = defaultdict(int)
    for e in employes:
        cle = (getattr(e, "entite", None) or "MA").upper()
        totaux[cle] += valeur(e)
        effectifs[cle] += 1

    return [
        {
            "entite": cle,
            "devise": devise_entite(cle),
            "total": round(totaux[cle], 2),
            "effectif": effectifs[cle],
        }
        for cle in ("MA", "FR")
        if cle in totaux
    ]


# ── Filtre par entité ────────────────────────────────────────────────────────

ENTITES = ("MA", "FR")

LIBELLES_ENTITE = {"MA": "ARRA Maroc", "FR": "ARRA France"}


def normaliser_entite(valeur: str | None) -> str | None:
    """« ma » → « MA ». Rend None pour « toutes les entités ».

    ⚠️ Une valeur inconnue rend **None** plutôt que de filtrer sur rien : un
    filtre mal orthographié qui viderait la liste ferait croire à un effectif
    vide. Mieux vaut montrer tout le monde que faire disparaître des salariés.
    """
    code = (valeur or "").strip().upper()
    return code if code in ENTITES else None
