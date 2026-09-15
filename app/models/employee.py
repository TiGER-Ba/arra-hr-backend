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
    # Entité employeur : « MA » (ARRA Maroc) ou « FR » (ARRA France).
    # Détermine le calendrier de jours fériés appliqué à sa feuille de temps.
    entite: Mapped[str] = mapped_column(String(2), nullable=False, default="MA")
    type_contrat: Mapped[str] = mapped_column(String(20), default="CDI")  # CDI | CDD | Stage | Freelance
    # Célibataire | Marié(e) | Divorcé(e) | Veuf(ve). Nullable en base pour les
    # fiches antérieures à son ajout ; exigé à la saisie.
    situation_familiale: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Renseigné uniquement pour un salarié marié — remis à NULL sinon, pour que
    # la donnée stockée corresponde toujours à ce que le formulaire affiche.
    # 0 est une valeur valide et se distingue de « non renseigné ».
    nombre_enfants: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_naissance: Mapped[date | None] = mapped_column(Date, nullable=True)
    sexe: Mapped[str | None] = mapped_column(String(1), nullable=True)  # M | F
    nationalite: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Début de la carrière, y compris hors ARRA : sert à calculer l'expérience
    # totale, distincte de l'ancienneté dans l'entreprise (date_embauche).
    date_premiere_experience: Mapped[date | None] = mapped_column(Date, nullable=True)
    # CIMR au Maroc. Facultatif : tous les salariés n'y sont pas affiliés.
    numero_retraite: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cnss: Mapped[str | None] = mapped_column(String(30), nullable=True)
    adresse: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telephone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    utilisateur: Mapped["Utilisateur"] = relationship("Utilisateur", back_populates="employe")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="employe")
    demandes: Mapped[list["Demande"]] = relationship("Demande", back_populates="employe")
    depot_documents: Mapped[list["DepotDocument"]] = relationship("DepotDocument", back_populates="employe", cascade="all, delete-orphan")
    soldes: Mapped[list["SoldeEmploye"]] = relationship("SoldeEmploye", back_populates="employe", cascade="all, delete-orphan")
