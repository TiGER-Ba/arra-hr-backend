from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.demande import Demande
from app.models.solde import SOLDE_TYPES, MouvementSolde, SoldeEmploye


def initialiser_soldes_par_defaut(db: Session, employe_id: int, annee: int | None = None):
    """Crée les soldes par défaut pour un nouvel employé."""
    annee = annee or datetime.now().year
    for type_solde, config in SOLDE_TYPES.items():
        existe = db.query(SoldeEmploye).filter(
            SoldeEmploye.employe_id == employe_id,
            SoldeEmploye.type == type_solde,
            SoldeEmploye.annee_reference == annee,
        ).first()
        if existe:
            continue
        db.add(SoldeEmploye(
            employe_id=employe_id,
            type=type_solde,
            unite=config["unite"],
            quota_total=config["default_quota"],
            consomme=0,
            annee_reference=annee,
        ))
    db.commit()


def get_solde(db: Session, employe_id: int, type_solde: str, annee: int | None = None) -> SoldeEmploye | None:
    annee = annee or datetime.now().year
    return db.query(SoldeEmploye).filter(
        SoldeEmploye.employe_id == employe_id,
        SoldeEmploye.type == type_solde,
        SoldeEmploye.annee_reference == annee,
    ).first()


def ajuster_solde(
    db: Session,
    solde: SoldeEmploye,
    delta: float,
    motif: str,
    demande_id: int | None = None,
    rh_id: int | None = None,
):
    """Crédit (delta > 0) ou débit (delta < 0) sur un solde + enregistre le mouvement."""
    solde.consomme = float(solde.consomme) - float(delta)  # delta positif = crédit (réduit consommé)
    db.add(MouvementSolde(
        solde_id=solde.id,
        delta=delta,
        motif=motif,
        demande_id=demande_id,
        cree_par_rh_id=rh_id,
    ))
    db.commit()
    db.refresh(solde)


def _compter_jours(date_debut: str, date_fin: str) -> int:
    """Calcule le nombre de jours OUVRABLES (lundi-vendredi) entre deux dates ISO inclusives.
    Les samedis et dimanches ne sont pas comptés.
    """
    try:
        d1 = date.fromisoformat(str(date_debut)[:10])
        d2 = date.fromisoformat(str(date_fin)[:10])
    except (ValueError, TypeError):
        return 0
    if d2 < d1:
        return 0
    jours = 0
    cur = d1
    while cur <= d2:
        if cur.weekday() < 5:  # 0=lundi, 4=vendredi
            jours += 1
        cur += timedelta(days=1)
    return jours


def calculer_date_fin_ouvrables(date_debut_iso: str, nombre_jours: int) -> str | None:
    """À partir d'une date de début + N jours ouvrables, calcule la date de fin.
    Exemple : 10/06/2026 (mercredi) + 5 jours ouvrables → 16/06/2026 (mardi)
    car samedi 13 et dimanche 14 sont sautés.
    """
    try:
        d = date.fromisoformat(str(date_debut_iso)[:10])
    except (ValueError, TypeError):
        return None
    if nombre_jours <= 0:
        return None
    # Avancer jusqu'au premier jour ouvrable si on commence un week-end
    while d.weekday() >= 5:
        d += timedelta(days=1)
    jours_comptes = 1  # le jour de début lui-même
    fin = d
    while jours_comptes < nombre_jours:
        fin += timedelta(days=1)
        if fin.weekday() < 5:
            jours_comptes += 1
    return fin.isoformat()


def appliquer_deduction_sur_validation(db: Session, demande: Demande, rh_id: int | None = None):
    """
    Appelée à la validation d'une demande pour décrémenter automatiquement
    le solde correspondant. Aucune erreur fatale si pas de solde dispo.
    """
    donnees = demande.donnees_collectees or {}
    annee = datetime.now().year

    if demande.type == "demande_conge":
        # Priorité : nombre_jours explicite fourni par l'utilisateur, sinon calcul jours ouvrables
        nb_explicite = donnees.get("nombre_jours")
        if nb_explicite:
            try:
                jours = int(float(nb_explicite))
            except (ValueError, TypeError):
                jours = _compter_jours(donnees.get("date_debut"), donnees.get("date_fin"))
        else:
            jours = _compter_jours(donnees.get("date_debut"), donnees.get("date_fin"))
        if jours <= 0:
            return
        # Pour les congés maladie : utiliser le solde conge_maladie sinon conge_annuel
        type_conge = (donnees.get("type_conge") or "").lower()
        type_solde = "conge_maladie" if "maladie" in type_conge else "conge_annuel"
        solde = get_solde(db, demande.employe_id, type_solde, annee)
        if solde:
            ajuster_solde(
                db, solde,
                delta=-jours,
                motif=f"Congé du {donnees.get('date_debut')} au {donnees.get('date_fin')} ({jours} j ouvrables)",
                demande_id=demande.id,
                rh_id=rh_id,
            )

    elif demande.type == "demande_formation":
        nb_explicite = donnees.get("nombre_jours")
        if nb_explicite:
            try:
                jours = int(float(nb_explicite))
            except (ValueError, TypeError):
                jours = _compter_jours(donnees.get("date_debut"), donnees.get("date_fin"))
        else:
            jours = _compter_jours(donnees.get("date_debut"), donnees.get("date_fin"))
        if jours <= 0:
            return
        solde = get_solde(db, demande.employe_id, "formation", annee)
        if solde:
            ajuster_solde(
                db, solde,
                delta=-jours,
                motif=f"Formation : {donnees.get('nom_formation', '')} ({jours} j)",
                demande_id=demande.id,
                rh_id=rh_id,
            )

    elif demande.type == "demande_avance_salaire":
        try:
            montant = float(donnees.get("montant", 0))
        except (ValueError, TypeError):
            montant = 0
        if montant <= 0:
            return
        solde = get_solde(db, demande.employe_id, "avance_salaire_plafond", annee)
        if solde:
            ajuster_solde(
                db, solde,
                delta=-montant,
                motif=f"Avance sur salaire : {montant} MAD",
                demande_id=demande.id,
                rh_id=rh_id,
            )
