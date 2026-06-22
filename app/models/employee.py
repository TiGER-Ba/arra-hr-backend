from datetime import date, datetime
from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Employe(Base):
    __tablename__ = "employes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    utilisateur_id: Mapped[int] = mapped_column(Integer, ForeignKey("utilisateurs.id", ondelete="CASCADE"), unique=True, nullable=False)
    matricule: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    poste: Mapped[str] = mapped_column(String(100), nullable=False)
    departement: Mapped[str] = mapped_column(String(100), nullable=False)
    salaire_base: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    date_embauche: Mapped[date] = mapped_column(Date, nullable=False)
    statut: Mapped[str] = mapped_column(String(20), default="actif")  # actif | inactif | suspendu
    type_contrat: Mapped[str] = mapped_column(String(20), default="CDI")  # CDI | CDD | Stage | Freelance
    cin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cnss: Mapped[str | None] = mapped_column(String(30), nullable=True)
    adresse: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telephone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    utilisateur: Mapped["Utilisateur"] = relationship("Utilisateur", back_populates="employe")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="employe")
    demandes: Mapped[list["Demande"]] = relationship("Demande", back_populates="employe")
    depot_documents: Mapped[list["DepotDocument"]] = relationship("DepotDocument", back_populates="employe", cascade="all, delete-orphan")
    soldes: Mapped[list["SoldeEmploye"]] = relationship("SoldeEmploye", back_populates="employe", cascade="all, delete-orphan")
