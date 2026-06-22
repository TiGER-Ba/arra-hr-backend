from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    demande_id: Mapped[int] = mapped_column(Integer, ForeignKey("demandes.id"), nullable=False)
    rh_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("rh.id"), nullable=True)
    template_id: Mapped[int] = mapped_column(Integer, ForeignKey("templates.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    chemin_fichier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    statut: Mapped[str] = mapped_column(String(20), default="genere", index=True)  # genere | valide | rejete | envoye
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    demande: Mapped["Demande"] = relationship("Demande", back_populates="documents")
    rh: Mapped["RH"] = relationship("RH", back_populates="documents")
    template: Mapped["Template"] = relationship("Template", back_populates="documents")
