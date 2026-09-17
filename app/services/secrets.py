"""Chiffrement des secrets stockés dans la table `parametrage`.

⚠️ Pourquoi. Les secrets de l'application (SMTP, clés d'API) y vivent **en
clair**. Pour le mot de passe Nextcloud c'est une autre échelle de risque : il
ouvre le drive partagé de l'entreprise, **`6.9 Recrutement` compris**, qui n'a
rien à voir avec cette application. Une sauvegarde PostgreSQL égarée suffirait.

La clé est **dérivée de `SECRET_KEY`** plutôt que stockée à part : il n'y a
qu'un secret à protéger, et `verifier_configuration()` garantit déjà qu'en
production elle n'est ni celle par défaut ni trop courte — donc une graine à
forte entropie. Un simple SHA-256 avec séparation de domaine suffit à en tirer
une clé ; un dérivateur lent (PBKDF2, scrypt) ne protégerait que contre une
attaque par dictionnaire, hors sujet sur une clé aléatoire de 32 caractères.

⚠️ **Changer `SECRET_KEY` rend les valeurs chiffrées illisibles.** C'est
assumé : il faut ressaisir le mot de passe. `dechiffrer()` lève alors
`SecretIllisible`, que l'appelant traduit en message clair — jamais en trace
d'erreur incompréhensible.

Le préfixe `enc:v1:` distingue une valeur chiffrée d'une ancienne valeur en
clair. Les deux cohabitent : `dechiffrer()` rend telle quelle une valeur sans
préfixe. C'est ce qui permettra de migrer SMTP et Groq plus tard sans rien
casser au passage.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

PREFIXE = "enc:v1:"

# Sépare cet usage de tout autre dérivé de SECRET_KEY (les JWT, par exemple) :
# deux usages ne doivent jamais aboutir à la même clé.
_DOMAINE = b"arra-parametrage-chiffrement-v1|"


class SecretIllisible(Exception):
    """Valeur chiffrée qui ne se déchiffre pas — en pratique, SECRET_KEY a changé."""


def _cle() -> bytes:
    graine = (settings.SECRET_KEY or "").encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(_DOMAINE + graine).digest())


def est_chiffre(valeur: str | None) -> bool:
    return bool(valeur) and valeur.startswith(PREFIXE)


def chiffrer(valeur: str | None) -> str:
    """Chiffre une valeur. Une valeur vide reste vide — rien à protéger."""
    if not valeur:
        return ""
    if est_chiffre(valeur):
        return valeur  # déjà chiffrée : ne pas empiler les couches
    jeton = Fernet(_cle()).encrypt(valeur.encode("utf-8")).decode("ascii")
    return f"{PREFIXE}{jeton}"


def dechiffrer(valeur: str | None) -> str:
    """Valeur en clair.

    Une valeur **sans préfixe** est rendue telle quelle : les secrets écrits
    avant ce module restent lisibles, sinon la mise à jour aurait coupé l'envoi
    des emails du jour au lendemain.
    """
    if not valeur:
        return ""
    if not est_chiffre(valeur):
        return valeur
    try:
        return Fernet(_cle()).decrypt(valeur[len(PREFIXE):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as e:
        raise SecretIllisible(
            "Le secret enregistré ne peut plus être déchiffré : SECRET_KEY a "
            "probablement changé. Ressaisissez-le dans le paramétrage."
        ) from e
