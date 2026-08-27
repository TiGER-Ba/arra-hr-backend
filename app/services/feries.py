"""Jours fériés — Maroc et France.

ARRA a deux entités : chaque salarié est rattaché à l'une ou l'autre, et ne voit
que le calendrier correspondant.

Trois familles de fériés :
  - **fixes** (grégorien) : calculables des années à l'avance, Maroc et France ;
  - **mobiles pascaux** (France) : Lundi de Pâques, Ascension, Pentecôte —
    calculables exactement à partir de la date de Pâques ;
  - **religieux musulmans** (Maroc) : calendrier hégirien, la date grégorienne
    dépend de l'observation lunaire officielle → **non calculable de façon
    fiable**, ils sont saisis par l'administrateur.
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.pointage import JourFerie

# Codes d'entité employeur
PAYS = ("MA", "FR")
LIBELLES_PAYS = {"MA": "Maroc", "FR": "France"}

# ── Maroc : fêtes nationales à date fixe ─────────────────────────────────────
FERIES_FIXES_MA = [
    (1, 1, "Nouvel An"),
    (1, 11, "Manifeste de l'Indépendance"),
    (5, 1, "Fête du Travail"),
    (7, 30, "Fête du Trône"),
    (8, 14, "Allégeance Oued Eddahab"),
    (8, 20, "Révolution du Roi et du Peuple"),
    (8, 21, "Fête de la Jeunesse"),
    (11, 6, "Marche Verte"),
    (11, 18, "Fête de l'Indépendance"),
]

# ── France : fériés à date fixe ──────────────────────────────────────────────
FERIES_FIXES_FR = [
    (1, 1, "Jour de l'An"),
    (5, 1, "Fête du Travail"),
    (5, 8, "Victoire 1945"),
    (7, 14, "Fête Nationale"),
    (8, 15, "Assomption"),
    (11, 1, "Toussaint"),
    (11, 11, "Armistice 1918"),
    (12, 25, "Noël"),
]

# Fêtes religieuses musulmanes : libellés proposés à la saisie manuelle
FERIES_RELIGIEUX_LABELS = [
    "Aïd al-Fitr", "Aïd al-Fitr (2ᵉ jour)",
    "Aïd al-Adha", "Aïd al-Adha (2ᵉ jour)",
    "Nouvel An de l'Hégire", "Aïd al-Mawlid", "Aïd al-Mawlid (2ᵉ jour)",
]


def _paques(annee: int) -> date:
    """Dimanche de Pâques (algorithme de Meeus/Jones/Butcher, calendrier grégorien).

    Contrairement au calendrier hégirien, Pâques est déterministe : les fériés
    français qui en dépendent peuvent donc être générés automatiquement.
    """
    a = annee % 19
    b, c = divmod(annee, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    mois, jour = divmod(h + l - 7 * m + 114, 31)
    return date(annee, mois, jour + 1)


def feries_mobiles_fr(annee: int) -> list[tuple[date, str]]:
    """Fériés français dépendant de Pâques."""
    p = _paques(annee)
    return [
        (p + timedelta(days=1), "Lundi de Pâques"),
        (p + timedelta(days=39), "Ascension"),
        (p + timedelta(days=50), "Lundi de Pentecôte"),
    ]


def feries_calcules(annee: int, pays: str) -> list[dict]:
    """Fériés générables automatiquement pour une année et une entité."""
    if pays == "FR":
        fixes = [
            {"date": date(annee, m, j), "libelle": lib}
            for m, j, lib in FERIES_FIXES_FR
        ]
        mobiles = [{"date": d, "libelle": lib} for d, lib in feries_mobiles_fr(annee)]
        return sorted(fixes + mobiles, key=lambda x: x["date"])
    return [
        {"date": date(annee, m, j), "libelle": lib}
        for m, j, lib in FERIES_FIXES_MA
    ]


def assurer_feries(db: Session, annee: int, pays: str) -> int:
    """Insère les fériés calculables manquants. Renvoie le nombre créé."""
    if pays not in PAYS:
        return 0
    existantes = {
        f.date_jour for f in db.query(JourFerie).filter(
            JourFerie.annee == annee, JourFerie.pays == pays
        ).all()
    }
    crees = 0
    for f in feries_calcules(annee, pays):
        if f["date"] not in existantes:
            db.add(JourFerie(
                date_jour=f["date"], libelle=f["libelle"],
                annee=annee, pays=pays, fixe=True,
            ))
            crees += 1
    if crees:
        db.commit()
    return crees


def feries_du_mois(db: Session, annee: int, mois: int, pays: str = "MA") -> dict[str, str]:
    """{date ISO: libellé} pour le mois et l'entité — pré-remplit le pointage."""
    assurer_feries(db, annee, pays)
    lignes = db.query(JourFerie).filter(
        JourFerie.annee == annee, JourFerie.pays == pays
    ).all()
    return {
        f.date_jour.isoformat(): f.libelle
        for f in lignes
        if f.date_jour.month == mois
    }


def pays_employe(employe) -> str:
    """Entité de rattachement du salarié (Maroc par défaut)."""
    return getattr(employe, "entite", None) or "MA"
