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
    # Salaire mensuel d'un INTERNE. Laissé à 0 pour un externe, qui est facturé
    # au TJM : la colonne reste NOT NULL pour ne pas toucher aux fiches en base.
    salaire_base: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    # Tarif journalier d'un EXTERNE (freelance, prestataire). Null pour un
    # salarié. À ne pas confondre avec Affectation.tjm, négocié par projet :
    # celui-ci est le tarif par défaut de la personne.
    tjm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    # ── Société portant le prestataire (type_contrat « Prestataire ») ────────
    # Le contrat de prestation lie ARRA à une SOCIÉTÉ, la personne n'y figurant
    # que comme ressource mise à disposition : ses mentions légales sont donc
    # indispensables au document. Saisies sur la fiche plutôt que rattachées au
    # CRM — une société de portage n'est ni un client ni un fournisseur suivi.
    presta_societe: Mapped[str | None] = mapped_column(String(150), nullable=True)
    presta_forme: Mapped[str | None] = mapped_column(String(80), nullable=True)
    presta_capital: Mapped[str | None] = mapped_column(String(60), nullable=True)
    presta_rc: Mapped[str | None] = mapped_column(String(60), nullable=True)
    presta_siege: Mapped[str | None] = mapped_column(String(255), nullable=True)
    presta_gerant: Mapped[str | None] = mapped_column(String(150), nullable=True)
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
    # Les contrats CDI/CDD/AE écrivent « Né le … à … » : le lieu est requis
    lieu_naissance: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sexe: Mapped[str | None] = mapped_column(String(1), nullable=True)  # M | F
    nationalite: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Début de la carrière, y compris hors ARRA : sert à calculer l'expérience
    # totale, distincte de l'ancienneté dans l'entreprise (date_embauche).
    date_premiere_experience: Mapped[date | None] = mapped_column(Date, nullable=True)
    # CIMR au Maroc. Facultatif : tous les salariés n'y sont pas affiliés.
    numero_retraite: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # RIB / IBAN de virement. Facultatif : il n'est pas toujours connu à
    # l'embauche, et un externe facture parfois depuis un autre compte.
    rib: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cnss: Mapped[str | None] = mapped_column(String(30), nullable=True)
    adresse: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telephone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    utilisateur: Mapped["Utilisateur"] = relationship("Utilisateur", back_populates="employe")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="employe")
    demandes: Mapped[list["Demande"]] = relationship("Demande", back_populates="employe")
    depot_documents: Mapped[list["DepotDocument"]] = relationship("DepotDocument", back_populates="employe", cascade="all, delete-orphan")
    soldes: Mapped[list["SoldeEmploye"]] = relationship("SoldeEmploye", back_populates="employe", cascade="all, delete-orphan")
