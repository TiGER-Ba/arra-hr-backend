"""Envoi d'email via SMTP.

Config lue depuis la table `Parametrage` (clés `smtp_*`), fallback sur le `.env`
(settings). Si le SMTP n'est pas configuré, `send_email` renvoie False sans lever
d'exception : l'appelant retombe alors sur l'affichage d'un lien copiable.
"""
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.config import settings
from app.services.parametrage import get_param


def smtp_config(db: Session) -> dict:
    port_raw = get_param(db, "smtp_port", str(settings.SMTP_PORT)) or "587"
    try:
        port = int(port_raw)
    except ValueError:
        port = 587
    user = get_param(db, "smtp_user", settings.SMTP_USER)
    return {
        "host": get_param(db, "smtp_host", settings.SMTP_HOST),
        "port": port,
        "user": user,
        "password": get_param(db, "smtp_password", settings.SMTP_PASSWORD),
        "from": get_param(db, "smtp_from", settings.SMTP_FROM) or user,
    }


def is_configured(db: Session) -> bool:
    cfg = smtp_config(db)
    return bool(cfg["host"] and cfg["user"] and cfg["password"])


def _envoyer(cfg: dict, msg: EmailMessage) -> None:
    """Ouvre la bonne connexion selon le port et envoie. Lève en cas d'échec.

    Port 465 = SSL implicite (cas des boîtes LWS France) ; 587/25 = STARTTLS.
    L'ancienne version ne gérait que STARTTLS et échouait donc sur LWS en 465.
    """
    contexte = ssl.create_default_context()
    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=contexte, timeout=25) as serveur:
            serveur.login(cfg["user"], cfg["password"])
            serveur.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=25) as serveur:
            serveur.ehlo()
            try:
                serveur.starttls(context=contexte)
                serveur.ehlo()
            except smtplib.SMTPNotSupportedError:
                pass  # serveur sans TLS explicite (réseau interne)
            serveur.login(cfg["user"], cfg["password"])
            serveur.send_message(msg)


def _construire(cfg: dict, to: str, subject: str, html: str, text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"] or cfg["user"]
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def send_email(db: Session, to: str, subject: str, html: str, text: str) -> bool:
    """Envoie un email. Renvoie True si envoyé, False si SMTP non configuré/échec."""
    cfg = smtp_config(db)
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        return False
    try:
        _envoyer(cfg, _construire(cfg, to, subject, html, text))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[email] échec envoi à {to} : {e}")
        return False


def envoyer_test(db: Session, to: str) -> None:
    """Envoi de vérification. Lève ValueError avec un message exploitable par l'admin."""
    cfg = smtp_config(db)
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        raise ValueError("SMTP incomplet : serveur, utilisateur et mot de passe sont requis.")

    msg = _construire(
        cfg, to,
        "Test de configuration — Plateforme RH ARRA",
        "<p>Cet email confirme que la messagerie de la plateforme RH est correctement configurée.</p>",
        "Cet email confirme que la messagerie de la plateforme RH est correctement configurée.",
    )
    try:
        _envoyer(cfg, msg)
    except smtplib.SMTPAuthenticationError:
        raise ValueError("Identifiants refusés par le serveur. Vérifiez l'adresse complète et le mot de passe.")
    except smtplib.SMTPConnectError:
        raise ValueError(f"Connexion impossible à {cfg['host']}:{cfg['port']}. Vérifiez le serveur et le port.")
    except smtplib.SMTPSenderRefused:
        raise ValueError("Expéditeur refusé : il doit correspondre à la boîte utilisée pour l'authentification.")
    except ssl.SSLError:
        raise ValueError(
            f"Erreur TLS sur le port {cfg['port']}. Utilisez 465 pour SSL/TLS ou 587 pour STARTTLS."
        )
    except (TimeoutError, OSError) as e:
        raise ValueError(
            f"Serveur injoignable ({cfg['host']}:{cfg['port']}) — {e}. "
            "Sur un VPS, vérifiez que le port sortant n'est pas bloqué."
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Échec de l'envoi : {e}")
