"""Cycle de vie d'une fiche salarié — source unique de vérité.

```
brouillon ──→ actif interne ⇄ actif production ──→ quitté ──→ compte désactivé
    └───────→ désistement ────────────────────────────────────→ compte désactivé
```

Deux statuts **ferment** le dossier (`désistement`, `quitté`) et désactivent le
compte ; deux le tiennent **ouvert** (`actif interne`, `actif production`). Le
`brouillon` est une fiche en cours de saisie : elle échappe aux champs
obligatoires et n'envoie aucune invitation.

⚠️ `Employe.statut` décrit la **fiche** ; `Utilisateur.is_active` décrit l'**accès
à l'application**. Les deux sont liés dans un seul sens : fermer le dossier coupe
l'accès (`compte_doit_etre_actif`). L'inverse n'est pas vrai — on peut suspendre
un accès sans que la personne ait quitté l'entreprise.

Les motifs de fin sont **séparés par nature de contrat** : « Démission » et
« Licenciement » relèvent du droit du travail et n'ont aucun sens pour un
freelance, dont la mission s'arrête ou se résilie.
"""

BROUILLON = "brouillon"
ACTIF_INTERNE = "actif_interne"
ACTIF_PRODUCTION = "actif_production"
DESISTEMENT = "desistement"
QUITTE = "quitte"

# L'ordre est celui du cycle de vie : il pilote l'affichage de la liste.
STATUTS: dict[str, str] = {
    BROUILLON: "Brouillon",
    ACTIF_INTERNE: "Actif interne",
    ACTIF_PRODUCTION: "Actif production",
    DESISTEMENT: "Désistement",
    QUITTE: "Quitté",
}

#: Dossier ouvert — la personne fait partie de l'effectif.
STATUTS_ACTIFS = (ACTIF_INTERNE, ACTIF_PRODUCTION)
#: Dossier clos — l'accès à l'application est coupé.
STATUTS_CLOS = (DESISTEMENT, QUITTE)

# Motifs de rupture d'un contrat de travail (CDI, CDD, Stage).
MOTIFS_FIN_INTERNE = (
    "Rupture conventionnelle",
    "Démission",
    "Licenciement",
    "Rupture période d'essai",
    # Un CDD ou un stage qui arrive simplement à son terme n'est aucune des
    # quatre ruptures ci-dessus : sans ce motif, le RH devrait mentir.
    "Fin de contrat à terme",
)

# Fin d'une collaboration externe (Freelance, Prestataire). Ni démission ni
# licenciement : il n'y a pas de lien de subordination.
MOTIFS_FIN_EXTERNE = (
    "Fin de mission",
    "Résiliation anticipée",
    "Non-renouvellement",
)


def motifs_fin(type_contrat: str | None) -> tuple[str, ...]:
    """Motifs de fin proposés, selon la nature du contrat."""
    from app.routers.users import est_externe

    return MOTIFS_FIN_EXTERNE if est_externe(type_contrat) else MOTIFS_FIN_INTERNE


def libelle(statut: str | None) -> str:
    return STATUTS.get(statut or "", statut or "—")


def est_actif(statut: str | None) -> bool:
    return statut in STATUTS_ACTIFS


def est_clos(statut: str | None) -> bool:
    return statut in STATUTS_CLOS


def compte_doit_etre_actif(statut: str | None) -> bool:
    """Un dossier clos coupe l'accès ; tout le reste le laisse ouvert.

    Le brouillon reste « actif » au sens du compte : l'invitation n'est
    simplement pas envoyée tant que la fiche n'est pas activée.
    """
    return not est_clos(statut)


def exige_fiche_complete(statut: str | None) -> bool:
    """Le brouillon est la seule dérogation aux champs obligatoires.

    Une fiche qu'on ne peut pas enregistrer tant qu'elle n'est pas complète
    oblige à tout saisir d'un coup ; le brouillon permet de commencer avec ce
    qu'on a. Les champs redeviennent exigés au passage en actif.
    """
    return statut != BROUILLON
