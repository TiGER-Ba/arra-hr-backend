"""Édition visuelle des modèles, pour une équipe non technique.

Le problème : un modèle est du HTML + Jinja. Donné tel quel à un éditeur de
texte enrichi, les balises `{% if %}` et `{{ variable }}` se font détruire au
premier clic — et un modèle cassé fait échouer toutes les générations suivantes.

La solution : **découper le modèle en trois zones** avant de le confier à
l'éditeur.

1. `prologue` / `epilogue` — le `<style>`, l'en-tête HTML. Techniques : ils ne
   sont jamais exposés à l'éditeur visuel, donc jamais abîmés.
2. `blocs` — les `{% if %}…{% endif %}` (logo, signature, cachet). Remplacés
   dans le corps par un JETON opaque que l'éditeur affiche comme une pastille
   verrouillée. L'utilisateur peut la déplacer ou la retirer, jamais la
   corrompre : le serveur réinjecte la source d'origine à l'enregistrement.
3. `corps` — le reste : la prose, éditable librement. Les `{{ variable }}` y
   deviennent des pastilles nommées.

Le corps reste un fragment HTML **bien formé**, donc affichable dans un
`contenteditable` avec le style du document : l'utilisateur voit sa page comme
dans un traitement de texte.

⚠️ Invariant vérifié par `test_modele_visuel.py` :
`recomposer(decouper(x)) == x` pour tous les modèles livrés.
"""
import re

# Jeton de bloc protégé. Les chevrons mathématiques ne peuvent pas apparaître
# dans un modèle ni être produits par un éditeur : la substitution est sûre.
JETON_BLOC = "⟦BLOC:{}⟧"
MOTIF_JETON = re.compile(r"⟦BLOC:(\d+)⟧")

_OUVRANTS = ("if", "for", "block", "with", "macro", "filter", "raw", "call")

# Étiquettes lisibles pour les blocs les plus courants — sinon, on affiche la
# condition elle-même, ce qui reste compréhensible.
_ETIQUETTES = (
    ("logo_url", "Logo ARRA"),
    ("signature_url", "Signature et cachet"),
    ("cachet_url", "Signature et cachet"),
    ("date_naissance", "Date de naissance (si renseignée)"),
    ("lieu_naissance", "Lieu de naissance (si renseigné)"),
    ("presta_", "Mentions légales du prestataire"),
)


def _nom_tag(source: str) -> str:
    """Nom de la balise d'un `{% ... %}` : « if », « endif », « else »…"""
    interieur = source[2:-2].strip()
    return interieur.split()[0] if interieur else ""


def _etiquette(source: str) -> str:
    for motif, libelle in _ETIQUETTES:
        if motif in source:
            return libelle
    condition = source[2:source.index("%}")].strip() if "%}" in source else source
    return condition[:60]


def decouper(contenu_html: str) -> dict:
    """Sépare le modèle en prologue, corps éditable, épilogue et blocs protégés."""
    prologue, corps, epilogue = _separer_corps(contenu_html)
    corps_jetonne, blocs = _remplacer_blocs(corps)
    return {
        "prologue": prologue,
        "corps": corps_jetonne,
        "epilogue": epilogue,
        "blocs": blocs,
        "style": _extraire_style(prologue),
    }


def recomposer(contenu_original: str, corps_edite: str) -> str:
    """Reconstruit le modèle complet à partir du seul corps édité.

    Le prologue, l'épilogue et la SOURCE des blocs sont relus du modèle
    d'origine : l'éditeur ne peut donc ni toucher au style, ni altérer la
    logique Jinja. Il ne décide que de l'emplacement des blocs et du texte.
    """
    decoupe = decouper(contenu_original)
    blocs = decoupe["blocs"]

    def remettre(m):
        index = int(m.group(1))
        return blocs[index]["source"] if 0 <= index < len(blocs) else ""

    corps = MOTIF_JETON.sub(remettre, corps_edite)
    return decoupe["prologue"] + corps + decoupe["epilogue"]


def _separer_corps(contenu: str) -> tuple[str, str, str]:
    """Isole l'intérieur de <body>, ou tout le contenu si la balise est absente.

    Les contrats n'ont pas de `<body>` — ils commencent par un `<style>` suivi
    du contenu. On isole alors le style comme prologue.
    """
    debut = re.search(r"<body[^>]*>", contenu, re.I)
    fin = contenu.rfind("</body>")
    if debut and fin > debut.end():
        return contenu[:debut.end()], contenu[debut.end():fin], contenu[fin:]

    style = re.search(r"</style>", contenu, re.I)
    if style:
        return contenu[:style.end()], contenu[style.end():], ""

    return "", contenu, ""


def _extraire_style(prologue: str) -> str:
    """CSS du modèle, pour que l'éditeur affiche le document tel qu'il sera imprimé."""
    trouve = re.search(r"<style[^>]*>(.*?)</style>", prologue, re.I | re.S)
    return trouve.group(1) if trouve else ""


def _remplacer_blocs(corps: str) -> tuple[str, list[dict]]:
    """Remplace chaque bloc `{% … %}` équilibré par un jeton opaque.

    Les blocs imbriqués sont absorbés par le bloc extérieur : on ne conserve
    que le niveau le plus haut, qui reste cohérent une fois déplacé.
    """
    blocs: list[dict] = []
    sortie: list[str] = []
    position = 0

    for debut, fin in _blocs_equilibres(corps):
        sortie.append(corps[position:debut])
        source = corps[debut:fin]
        sortie.append(JETON_BLOC.format(len(blocs)))
        blocs.append({"source": source, "etiquette": _etiquette(source)})
        position = fin

    sortie.append(corps[position:])
    return "".join(sortie), blocs


def _blocs_equilibres(corps: str) -> list[tuple[int, int]]:
    """Positions (début, fin) des blocs Jinja de plus haut niveau."""
    balises = [(m.start(), m.end(), _nom_tag(m.group(0)))
               for m in re.finditer(r"{%.*?%}", corps, re.S)]

    blocs: list[tuple[int, int]] = []
    i = 0
    while i < len(balises):
        debut, fin_balise, nom = balises[i]
        if nom not in _OUVRANTS:
            # Une balise isolée ({% set %}, ou un {% endif %} orphelin) est
            # protégée telle quelle : elle n'a pas de contenu à embarquer.
            blocs.append((debut, fin_balise))
            i += 1
            continue

        profondeur = 1
        j = i + 1
        while j < len(balises) and profondeur > 0:
            _, fin_j, nom_j = balises[j]
            if nom_j in _OUVRANTS:
                profondeur += 1
            elif nom_j.startswith("end"):
                profondeur -= 1
                if profondeur == 0:
                    blocs.append((debut, fin_j))
                    break
            j += 1
        else:
            # Bloc jamais refermé : on protège la seule balise ouvrante plutôt
            # que d'avaler la fin du document.
            blocs.append((debut, fin_balise))
            j = i
        i = j + 1

    return blocs
