"""Jours fériés marocains.

Deux familles :
  - **fixes** (calendrier grégorien) : calculables des années à l'avance ;
  - **religieux** (calendrier hégirien) : la date grégorienne varie chaque année
    et dépend de l'observation lunaire officielle → ils ne peuvent PAS être
    calculés de façon fiable. Le RH les saisit, année par année.

Les fériés pré-remplissent le pointage sans être décomptés des congés.
"""
from datetime import date

from sqlalchemy.orm import Session

from app.models.pointage import JourFerie

# (mois, jour, libellé) — fêtes nationales à date fixe
FERIES_FIXES = [
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

# Fêtes religieuses : libellés proposés à la saisie (dates variables)
FERIES_RELIGIEUX_LABELS = [
    "Aïd al-Fitr", "Aïd al-Fitr (2ᵉ jour)",
    "Aïd al-Adha", "Aïd al-Adha (2ᵉ jour)",
    "Nouvel An de l'Hégire", "Aïd al-Mawlid", "Aïd al-Mawlid (2ᵉ jour)",
]


def feries_fixes_annee(annee: int) -> list[dict]:
    """Fériés à date fixe pour une année donnée."""
    return [
        {"date": date(annee, m, j).isoformat(), "libelle": libelle, "fixe": True}
        for m, j, libelle in FERIES_FIXES
    ]


def assurer_feries_fixes(db: Session, annee: int) -> int:
    """Insère les fériés fixes manquants pour l'année. Renvoie le nombre créé."""
    existantes = {
        f.date_jour for f in db.query(JourFerie).filter(JourFerie.annee == annee).all()
    }
    crees = 0
    for m, j, libelle in FERIES_FIXES:
        d = date(annee, m, j)
        if d not in existantes:
            db.add(JourFerie(date_jour=d, libelle=libelle, annee=annee, fixe=True))
            crees += 1
    if crees:
        db.commit()
    return crees


def feries_du_mois(db: Session, annee: int, mois: int) -> dict[str, str]:
    """{date ISO: libellé} pour le mois — sert à pré-remplir le pointage."""
    assurer_feries_fixes(db, annee)
    lignes = db.query(JourFerie).filter(JourFerie.annee == annee).all()
    return {
        f.date_jour.isoformat(): f.libelle
        for f in lignes
        if f.date_jour.month == mois
    }
