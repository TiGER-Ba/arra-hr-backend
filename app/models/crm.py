"""CRM — Sociétés, Projets et Affectations.

Périmètre volontairement resserré : ce qui est nécessaire pour rattacher le
pointage à un projet et savoir qui travaille sur quoi. Pas de facturation ni
d'achats à ce stade.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Nature de la relation commerciale
TYPES_SOCIETE = ("client", "fournisseur", "partenaire", "prospect")

# Mode d'exécution du projet
TYPES_MISSION = ("regie", "forfait", "interne")

STATUTS_PROJET = ("en_cours", "termine", "archive")


class Societe(Base):
    __tablename__ = "societes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nom: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="client")
    secteur: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Identifiants légaux marocains
    ice: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rc: Mapped[str | None] = mapped_column(String(50), nullable=True)

    ville: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pays: Mapped[str | None] = mapped_column(String(80), nullable=True, default="Maroc")
    site_web: Mapped[str | None] = mapped_column(String(200), nullable=True)
    telephone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    projets: Mapped[list["Projet"]] = relationship(
        "Projet", back_populates="societe", cascade="all, delete-orphan"
    )


class Projet(Base):
    __tablename__ = "projets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Référence courte affichée partout (ex. « PRJ28 »)
    reference: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    nom: Mapped[str] = mapped_column(String(150), nullable=False)

    # Null = projet interne (formation, R&D…), sans société cliente
    societe_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("societes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    type_mission: Mapped[str] = mapped_column(String(20), nullable=False, default="regie")
    statut: Mapped[str] = mapped_column(String(20), nullable=False, default="en_cours", index=True)

    date_debut: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_fin: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Responsable côté ARRA
    manager_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), nullable=True
    )
    # Interlocuteur côté client (texte libre : pas de module Contacts à ce stade)
    contact_client: Mapped[str | None] = mapped_column(String(150), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    societe: Mapped["Societe"] = relationship("Societe", back_populates="projets")
    affectations: Mapped[list["Affectation"]] = relationship(
        "Affectation", back_populates="projet", cascade="all, delete-orphan"
    )

    @property
    def libelle(self) -> str:
        """Intitulé affiché dans les listes déroulantes du pointage,
        au format Boond : « PRJ28 - Architecture - Segula Maroc Africa SA »."""
        client = self.societe.nom if self.societe else "Interne"
        return f"{self.reference} - {self.nom} - {client}"


class Affectation(Base):
    """Rattachement d'un salarié à un projet, sur une période donnée.

    Détermine les projets proposés à la saisie dans sa feuille de temps.
    """
    __tablename__ = "affectations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    projet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employe_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date_debut: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Tarif journalier facturé au client (informatif ici : pas de module finance)
    tjm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    actif: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    projet: Mapped["Projet"] = relationship("Projet", back_populates="affectations")
