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


def send_email(db: Session, to: str, subject: str, html: str, text: str) -> bool:
    """Envoie un email. Renvoie True si envoyé, False si SMTP non configuré/échec."""
    cfg = smtp_config(db)
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[email] échec envoi à {to} : {e}")
        return False
