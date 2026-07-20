from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class JournalAudit(Base):
    """Journal d'audit : trace des actions sensibles (gestion des comptes).

    Les infos de l'acteur sont dénormalisées (nom/rôle copiés) pour que la
    suppression ultérieure du compte acteur ne casse pas l'affichage.
    """
    __tablename__ = "journal_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    acteur_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acteur_nom: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acteur_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)  # ex. user.create
    cible_type: Mapped[str | None] = mapped_column(String(40), nullable=True)     # ex. utilisateur
    cible_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cible_libelle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
