"""Primitives de sécurité transverses : noms de fichiers, quotas d'upload,
limitation de débit (anti brute-force) et validation de la configuration.

Regroupé ici pour qu'il n'existe qu'UNE implémentation par garde-fou.
"""
import os
import re
import time
import unicodedata
from threading import Lock

from fastapi import HTTPException, UploadFile

# ─── Noms de fichiers ────────────────────────────────────────────────────────

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_LEADING_DOTS = re.compile(r"^\.+")


def safe_filename(filename: str | None, default: str = "document") -> str:
    """Nom de fichier sûr : sans chemin, sans caractère spécial, non vide.

    Neutralise les traversées de répertoire (« ../ », « ..\\ », chemins absolus,
    « C:\\… ») : on ne conserve QUE le nom de base, puis on filtre les caractères.
    """
    name = (filename or "").strip()
    # Coupe tout composant de chemin, quel que soit le séparateur (POSIX ou Windows)
    name = name.replace("\\", "/").split("/")[-1]
    name = os.path.basename(name)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = _UNSAFE_CHARS.sub("_", name)
    name = _LEADING_DOTS.sub("", name)  # évite « .htaccess » / fichiers cachés
    name = name.strip("._-")
    if not name:
        return default
    # Garde-fou longueur (limites du système de fichiers)
    base, ext = os.path.splitext(name)
    return base[:100] + ext[:20]


def safe_join(base_dir: str, *parts: str) -> str:
    """Assemble un chemin en garantissant qu'il reste SOUS base_dir."""
    base = os.path.abspath(base_dir)
    target = os.path.abspath(os.path.join(base, *parts))
    if target != base and not target.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="Chemin de fichier invalide")
    return target


# ─── Quotas d'upload ─────────────────────────────────────────────────────────

def read_upload_limited(file: UploadFile, max_mb: int, allowed_ext: set[str] | None = None) -> bytes:
    """Lit un upload en refusant les fichiers trop gros ou d'extension interdite.

    Lit en mémoire par blocs et s'arrête dès le dépassement : un client ne peut
    pas saturer le disque en annonçant une taille mensongère.
    """
    if allowed_ext is not None:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in allowed_ext:
            raise HTTPException(
                status_code=400,
                detail=f"Format non supporté. Acceptés : {', '.join(sorted(allowed_ext))}",
            )

    max_bytes = max_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"Fichier trop volumineux (max {max_mb} Mo)")
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="Fichier vide")
    return b"".join(chunks)


# ─── Limitation de débit (anti brute-force) ──────────────────────────────────

class RateLimiter:
    """Compteur glissant en mémoire (suffisant pour une instance unique).

    Pour un déploiement multi-workers, remplacer par un backend partagé (Redis).
    """

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.max_attempts:
                retry = int(self.window - (now - hits[0])) + 1
                raise HTTPException(
                    status_code=429,
                    detail=f"Trop de tentatives. Réessayez dans {retry} secondes.",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)
            self._hits[key] = hits
            # Purge opportuniste pour éviter une croissance mémoire non bornée
            if len(self._hits) > 5000:
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    self._hits.pop(k, None)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


# 10 tentatives de connexion par IP toutes les 5 minutes
login_limiter = RateLimiter(max_attempts=10, window_seconds=300)


# ─── Validation de la configuration au démarrage ─────────────────────────────

DEFAULT_SECRET = "changeme_at_least_32_chars_long_secret"


def verifier_configuration(settings) -> list[str]:
    """Contrôle la config. Fatal en production, simple avertissement en dev.

    Renvoie la liste des avertissements non bloquants.
    """
    env = (getattr(settings, "ENVIRONMENT", "development") or "development").lower()
    est_prod = env in ("production", "prod")
    erreurs: list[str] = []
    avertissements: list[str] = []

    secret = settings.SECRET_KEY or ""
    if secret == DEFAULT_SECRET or not secret:
        erreurs.append("SECRET_KEY utilise la valeur par défaut : les jetons JWT seraient falsifiables.")
    elif len(secret) < 32:
        erreurs.append("SECRET_KEY fait moins de 32 caractères.")

    origins = os.getenv("ALLOWED_ORIGINS", "")
    if est_prod:
        if not origins:
            erreurs.append("ALLOWED_ORIGINS n'est pas défini (CORS).")
        elif "*" in origins:
            erreurs.append("ALLOWED_ORIGINS contient '*' : incompatible avec les cookies/credentials.")
        if "sqlite" in (settings.DATABASE_URL or "").lower():
            avertissements.append("DATABASE_URL pointe vers SQLite en production.")

    if erreurs:
        message = "Configuration invalide :\n" + "\n".join(f"  - {e}" for e in erreurs)
        if est_prod:
            raise RuntimeError(
                message + "\n\nDéfinissez ces variables d'environnement avant de démarrer en production."
            )
        avertissements.extend(erreurs)

    return avertissements
