from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Types de soldes gérés
SOLDE_TYPES = {
    "conge_annuel":           {"label": "Congés annuels",            "unite": "jours", "default_quota": 18.0},
    "conge_maladie":          {"label": "Congés maladie",            "unite": "jours", "default_quota": 0.0},
    "formation":              {"label": "Jours de formation",        "unite": "jours", "default_quota": 5.0},
    "avance_salaire_plafond": {"label": "Plafond d'avance salaire",  "unite": "MAD",   "default_quota": 0.0},
    "teletravail":            {"label": "Jours de télétravail",      "unite": "jours", "default_quota": 0.0},
}


class SoldeEmploye(Base):
    __tablename__ = "soldes_employes"
    __table_args__ = (
        UniqueConstraint("employe_id", "type", "annee_reference", name="uq_solde_employe_type_annee"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    employe_id: Mapped[int] = mapped_column(Integer, ForeignKey("employes.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # cf SOLDE_TYPES
    unite: Mapped[str] = mapped_column(String(10), nullable=False, default="jours")  # jours | MAD
    quota_total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    consomme: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    annee_reference: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    employe: Mapped["Employe"] = relationship("Employe", back_populates="soldes")


class MouvementSolde(Base):
    """Historique des crédits/débits sur un solde."""
    __tablename__ = "mouvements_soldes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    solde_id: Mapped[int] = mapped_column(Integer, ForeignKey("soldes_employes.id", ondelete="CASCADE"), nullable=False, index=True)
    delta: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # négatif si débit, positif si crédit
    motif: Mapped[str] = mapped_column(String(255), nullable=False)
    demande_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("demandes.id", ondelete="SET NULL"), nullable=True)
    cree_par_rh_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("rh.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
