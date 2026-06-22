from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RH(Base):
    __tablename__ = "rh"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    utilisateur_id: Mapped[int] = mapped_column(Integer, ForeignKey("utilisateurs.id", ondelete="CASCADE"), unique=True, nullable=False)
    service: Mapped[str] = mapped_column(String(100), nullable=False)

    utilisateur: Mapped["Utilisateur"] = relationship("Utilisateur", back_populates="rh")
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="rh")
    depot_documents: Mapped[list["DepotDocument"]] = relationship("DepotDocument", back_populates="uploaded_by_rh")
