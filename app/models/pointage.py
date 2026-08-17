from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# ── Catégories (reprises du modèle Boond) ────────────────────────────────────
#   production : temps passé sur un projet client → facturable
#   absence    : congés, maladie, férié → non travaillé
#   interne    : formation, R&D, administratif → travaillé mais non facturable
CATEGORIES = ("production", "absence", "interne")

# Types d'absence (utilisés quand categorie == "absence")
ABSENCE_TYPES = {"conge_paye", "conge_sans_solde", "maladie", "ferie"}

LIBELLES_ABSENCE = {
    "conge_paye": "Congés payés",
    "conge_sans_solde": "Congés sans solde",
    "maladie": "Maladie",
    "ferie": "Férié",
}

# Types d'activité interne
LIBELLES_INTERNE = {
    "formation": "Formation",
    "administratif": "Administratif",
    "intercontrat": "Intercontrat",
}

# Conservé pour compatibilité avec l'ancien module (grille simple)
POINTAGE_TYPES = {
    "travaille": "Travaillé",
    **LIBELLES_ABSENCE,
}


class Pointage(Base):
    """Une ligne = un salarié, un jour, une activité, une valeur (0,5 ou 1 jour).

    ⚠️ Il n'y a PLUS de contrainte d'unicité par jour : un salarié peut répartir
    sa journée entre deux projets (0,5 + 0,5), ou cumuler une demi-journée
    travaillée et une demi-journée de congé.
    """
    __tablename__ = "pointages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    employe_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date_jour: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    categorie: Mapped[str] = mapped_column(String(20), nullable=False, default="absence")
    # Type d'absence ou d'activité interne ; « normale » pour la production
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Renseigné uniquement si categorie == "production"
    projet_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("projets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 1 = journée entière, 0.5 = demi-journée
    valeur: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, default=1)

    commentaire: Mapped[str | None] = mapped_column(String(255), nullable=True)


class JourFerie(Base):
    """Jour férié national. Les fêtes religieuses suivent le calendrier hégirien :
    leur date grégorienne varie chaque année, elles sont donc saisies par le RH."""
    __tablename__ = "jours_feries"
    __table_args__ = (
        UniqueConstraint("date_jour", name="uq_ferie_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date_jour: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    libelle: Mapped[str] = mapped_column(String(100), nullable=False)
    annee: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fixe: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FeuilleTemps(Base):
    """Statut mensuel de la feuille de temps d'un salarié.

    Circuit : brouillon → soumise → validee | rejetee
    (un rejet renvoie la feuille en modification, avec un motif).
    """
    __tablename__ = "feuilles_temps"
    __table_args__ = (
        UniqueConstraint("employe_id", "annee", "mois", name="uq_feuille_employe_mois"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    employe_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    annee: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mois: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 1-12
    statut: Mapped[str] = mapped_column(String(20), nullable=False, default="brouillon")

    # Traçabilité de la validation
    valide_par_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), nullable=True
    )
    valide_le: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    motif_rejet: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
