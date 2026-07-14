"""OCR local d'une pièce d'identité (CIN / passeport) — SANS LLM.

Stratégie :
  1) MRZ (zone lisible machine) — passeports (TD3) et CIN biométriques (TD1) :
     lecture fiable via un passage Tesseract dédié + parsing avec la lib `mrz`.
  2) Repli OCR plein texte (FR+EN) : pré-remplissage best-effort + n° CIN par regex.

Aucune donnée ne quitte le serveur. La sortie sert à PRÉ-REMPLIR un formulaire
que l'utilisateur valide toujours avant création (l'OCR est imparfait).
"""
import io
import re

_MRZ_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"


def _blank_result(message: str, raw_text: str = "") -> dict:
    return {
        "ok": False, "source": None, "nom": None, "prenom": None, "cin": None,
        "date_naissance": None, "nationalite": None, "sexe": None,
        "raw_text": raw_text, "message": message,
    }


def _ocr(image, lang: str, config: str = "") -> str:
    import pytesseract
    return pytesseract.image_to_string(image, lang=lang, config=config)


def _format_birth(yymmdd: str | None) -> str | None:
    if not yymmdd or len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    yy, mm, dd = yymmdd[:2], yymmdd[2:4], yymmdd[4:6]
    import datetime
    century = 19 if int(yy) > (datetime.date.today().year % 100) else 20
    return f"{dd}/{mm}/{century}{yy}"


def _clean_mrz_line(line: str) -> str:
    up = "".join(ch for ch in line.upper().replace(" ", "") if ch in _MRZ_CHARS)
    return up


def _find_mrz_block(text: str) -> list[str] | None:
    """Renvoie 2 ou 3 lignes MRZ consécutives, sinon None."""
    lines = [_clean_mrz_line(l) for l in text.splitlines()]
    lines = [l for l in lines if len(l) >= 28 and l.count("<") >= 2]
    if not lines:
        return None
    # On garde les 3 dernières lignes plausibles (la MRZ est en bas du document)
    return lines[-3:] if len(lines) >= 3 else lines[-2:]


def _pad(line: str, size: int) -> str:
    return (line + "<" * size)[:size]


def _parse_mrz(block: list[str]) -> dict | None:
    """Essaie TD1 (3x30), TD3 (2x44), TD2 (2x36)."""
    attempts = []
    if len(block) >= 3:
        attempts.append(("td1", [_pad(x, 30) for x in block[-3:]]))
    if len(block) >= 2:
        attempts.append(("td3", [_pad(x, 44) for x in block[-2:]]))
        attempts.append(("td2", [_pad(x, 36) for x in block[-2:]]))

    for kind, lines in attempts:
        try:
            if kind == "td1":
                from mrz.checker.td1 import TD1CodeChecker as Checker
            elif kind == "td3":
                from mrz.checker.td3 import TD3CodeChecker as Checker
            else:
                from mrz.checker.td2 import TD2CodeChecker as Checker
            checker = Checker("\n".join(lines), check_expiry=False)
            f = checker.fields()
        except Exception:
            continue
        surname = (getattr(f, "surname", "") or "").strip()
        name = (getattr(f, "name", "") or "").strip()
        doc = (getattr(f, "document_number", "") or "").replace("<", "").strip()
        if not (surname or name):
            continue
        return {
            "nom": surname or None,
            "prenom": name or None,
            "cin": doc or None,
            "nationalite": (getattr(f, "nationality", "") or "").strip() or None,
            "sexe": (getattr(f, "sex", "") or "").strip() or None,
            "date_naissance": _format_birth((getattr(f, "birth_date", "") or "").strip()),
        }
    return None


_CIN_RE = re.compile(r"\b([A-Z]{1,2}\d{4,7})\b")


def extract_id_fields(data: bytes, filename: str = "") -> dict:
    """Point d'entrée : bytes image → champs pré-remplis."""
    if filename.lower().endswith(".pdf"):
        return _blank_result("Merci de fournir une image (JPG / PNG) de la pièce, pas un PDF.")

    try:
        from PIL import Image
    except Exception:
        return _blank_result("Bibliothèque image (Pillow) indisponible sur le serveur.")

    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return _blank_result("Image illisible — réessayez avec une photo nette (JPG/PNG).")

    # Passage MRZ dédié (whitelist des caractères MRZ) + passage plein texte FR/EN
    try:
        mrz_text = _ocr(image, "eng", f"--psm 6 -c tessedit_char_whitelist={_MRZ_CHARS}")
    except Exception as e:
        if "TesseractNotFound" in type(e).__name__ or "tesseract" in str(e).lower():
            return _blank_result("OCR indisponible : Tesseract n'est pas installé sur le serveur.")
        mrz_text = ""
    try:
        full_text = _ocr(image, "fra+eng")
    except Exception:
        full_text = ""

    # 1) MRZ
    block = _find_mrz_block(mrz_text) or _find_mrz_block(full_text)
    if block:
        parsed = _parse_mrz(block)
        if parsed:
            return {"ok": True, "source": "mrz", "raw_text": full_text.strip(),
                    "message": "Extrait de la zone lisible machine (MRZ). Vérifiez avant de valider.",
                    **parsed}

    # 2) Repli : n° CIN par regex sur le texte plein
    cin = None
    m = _CIN_RE.search(full_text.upper())
    if m:
        cin = m.group(1)

    if not full_text.strip():
        return _blank_result("Aucun texte détecté — photo trop floue ou pièce non reconnue.")

    return {
        "ok": bool(cin), "source": "ocr", "nom": None, "prenom": None, "cin": cin,
        "date_naissance": None, "nationalite": None, "sexe": None,
        "raw_text": full_text.strip(),
        "message": "MRZ non détectée. N° d'identité déduit du texte ; complétez le nom/prénom manuellement.",
    }
