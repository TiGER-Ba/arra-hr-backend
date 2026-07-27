from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Types de pointage journalier (journées entières).
# « travaille » n'est pas stocké : un jour ouvré sans entrée = travaillé.
POINTAGE_TYPES = {
    "travaille": "Travaillé",
    "conge_paye": "Congé payé",
    "conge_sans_solde": "Congé sans solde",
    "maladie": "Maladie",
    "ferie": "Férié",
}
# Types d'ABSENCE réellement stockés (tout sauf « travaille »)
ABSENCE_TYPES = {"conge_paye", "conge_sans_solde", "maladie", "ferie"}


class Pointage(Base):
    """Une entrée = un jour d'ABSENCE/férié pour un employé (les jours travaillés
    sont implicites : jour ouvré sans entrée = travaillé)."""
    __tablename__ = "pointages"
    __table_args__ = (
        UniqueConstraint("employe_id", "date_jour", name="uq_pointage_employe_jour"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    employe_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date_jour: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False)  # cf ABSENCE_TYPES
    commentaire: Mapped[str | None] = mapped_column(String(255), nullable=True)


class FeuilleTemps(Base):
    """Statut mensuel de la feuille de temps d'un employé (brouillon | soumise)."""
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
    statut: Mapped[str] = mapped_column(String(20), nullable=False, default="brouillon")  # brouillon | soumise
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
