from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

CATEGORIES = (
    "bulletin_paie",
    "contrat_travail",
    "avenant_contrat",
    "attestation_externe",
    "autre",
)


class DepotDocument(Base):
    __tablename__ = "depot_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    employe_id: Mapped[int] = mapped_column(Integer, ForeignKey("employes.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by_rh_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("rh.id", ondelete="SET NULL"), nullable=True)

    categorie: Mapped[str] = mapped_column(String(50), nullable=False)  # voir CATEGORIES
    nom_fichier: Mapped[str] = mapped_column(String(255), nullable=False)  # nom d'affichage
    chemin_fichier: Mapped[str] = mapped_column(String(500), nullable=False)  # chemin physique
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Utile pour les bulletins de paie : retrouver facilement "mars 2025"
    mois: Mapped[str | None] = mapped_column(String(2), nullable=True)   # "01"–"12"
    annee: Mapped[int | None] = mapped_column(Integer, nullable=True)

    visible_employe: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    employe: Mapped["Employe"] = relationship("Employe", back_populates="depot_documents")
    uploaded_by_rh: Mapped["RH"] = relationship("RH", back_populates="depot_documents")
