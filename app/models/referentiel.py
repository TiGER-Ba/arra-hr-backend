from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Catégories gérées. Une liste de choix alimentée par l'usage, pas une
# nomenclature figée : le RH peut y ajouter une valeur depuis le formulaire.
CATEGORIE_NATIONALITE = "nationalite"
CATEGORIE_POSTE = "poste"
CATEGORIES = (CATEGORIE_NATIONALITE, CATEGORIE_POSTE)

# Valeurs proposées d'office à la première ouverture. « Marocaine » est la
# valeur par défaut du formulaire ; les autres évitent de repartir d'une liste
# vide sans prétendre être exhaustives.
VALEURS_INITIALES: dict[str, tuple[str, ...]] = {
    CATEGORIE_NATIONALITE: (
        "Marocaine", "Française", "Algérienne", "Tunisienne",
        "Sénégalaise", "Ivoirienne", "Espagnole", "Belge", "Canadienne",
    ),
    CATEGORIE_POSTE: (
        "Ingénieur Logiciel", "Consultant / Engineer", "Design Release Engineer",
        "Chef de Projet", "Analyste Financier", "Technicien", "Stagiaire",
    ),
}

NATIONALITE_DEFAUT = "Marocaine"


class ValeurReferentiel(Base):
    """Valeur d'une liste de choix (nationalités aujourd'hui, autres demain).

    Comme pour les départements, la valeur retenue est recopiée **en texte** sur
    la fiche salarié : retirer une entrée d'ici ne touche aucune fiche existante
    ni aucun document déjà émis.

    ⚠️ `departements` est une table antérieure qui fait la même chose pour les
    départements ; elle n'a pas été migrée ici pour ne pas toucher à une
    fonctionnalité en production. Toute NOUVELLE liste de choix passe par cette
    table-ci plutôt que par une table dédiée.
    """

    __tablename__ = "referentiels"
    __table_args__ = (
        UniqueConstraint("categorie", "valeur", name="uq_referentiel_categorie_valeur"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    categorie: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    valeur: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
