"""Profil RH d'un gestionnaire — résolu à la demande.

⚠️ Pourquoi ce module existe. La table `rh` ne portait de ligne que pour les
comptes de **rôle `rh`** : elle est créée à l'inscription (`routers/auth.py`) et
à la création de compte (`routers/users.py`), dans les deux cas sous
`if role == "rh"`. Un **administrateur n'en avait donc aucune**.

Or plusieurs actions RH tracent leur auteur par `rh_id` : validation d'une
demande (`Document.rh_id`), dépôt d'une pièce (`DepotDocument.uploaded_by_rh_id`),
ajustement d'un solde (`MouvementSolde.cree_par_rh_id`). L'ancien `_get_rh()`
répondait **404 « Profil RH introuvable »** quand il n'en trouvait pas — ce qui
interdisait à l'admin de valider une demande ou de déposer un document, alors
que `require_rh` l'avait bien laissé passer.

L'admin doit pouvoir tout ce que peut le RH. On crée donc la ligne au premier
besoin plutôt que de refuser l'action. Créer plutôt que rendre `rh_id` nul
préserve la piste d'audit : le document reste rattaché à la personne qui l'a
émis.
"""
from sqlalchemy.orm import Session

from app.models.rh import RH
from app.models.user import Utilisateur

SERVICE_PAR_DEFAUT = {
    "admin": "Direction",
    "rh": "Ressources Humaines",
}


def profil_rh(user: Utilisateur, db: Session) -> RH:
    """Ligne `rh` du gestionnaire, créée si elle manque.

    Appelée derrière `require_rh`, donc le rôle est déjà `rh` ou `admin`.
    """
    rh = db.query(RH).filter(RH.utilisateur_id == user.id).first()
    if rh:
        return rh

    rh = RH(
        utilisateur_id=user.id,
        service=SERVICE_PAR_DEFAUT.get(user.role, "Ressources Humaines"),
    )
    db.add(rh)
    db.flush()  # l'id est nécessaire tout de suite ; le commit reste à l'appelant
    return rh
