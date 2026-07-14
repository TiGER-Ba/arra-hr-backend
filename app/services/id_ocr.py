"""OCR local d'une pièce d'identité (CIN / passeport) — SANS LLM.

Stratégie :
  1) MRZ (zone lisible machine) — passeports (TD3) et CIN biométriques (TD1) :
     plusieurs passes Tesseract (psm 6 + psm 4, whitelist MRZ) sur une image
     pré-traitée, puis parsing avec la lib `mrz`. La MRZ est cherchée N'IMPORTE OÙ
     dans le texte (toutes les fenêtres de lignes consécutives), pas seulement en bas.
  2) Repli OCR plein texte (FR+EN) : n° CIN par regex.

Aucune donnée ne quitte le serveur. La sortie PRÉ-REMPLIT un formulaire que
l'utilisateur valide toujours avant création (l'OCR est imparfait).
"""
import io
import re

_MRZ_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_FMT = {"td1": (3, 30), "td3": (2, 44), "td2": (2, 36)}  # (nb lignes, longueur)


def _blank_result(message: str, raw_text: str = "") -> dict:
    return {
        "ok": False, "source": None, "nom": None, "prenom": None, "cin": None,
        "date_naissance": None, "nationalite": None, "sexe": None,
        "raw_text": raw_text, "message": message,
    }


def _ocr(image, lang: str, config: str = "") -> str:
    import pytesseract
    return pytesseract.image_to_string(image, lang=lang, config=config)


def _is_tesseract_missing(e: Exception) -> bool:
    return "TesseractNotFound" in type(e).__name__ or "tesseract" in str(e).lower()


def _format_birth(yymmdd: str | None) -> str | None:
    if not yymmdd or len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    yy, mm, dd = yymmdd[:2], yymmdd[2:4], yymmdd[4:6]
    import datetime
    century = 19 if int(yy) > (datetime.date.today().year % 100) else 20
    return f"{dd}/{mm}/{century}{yy}"


def _clean_mrz_line(line: str) -> str:
    return "".join(ch for ch in line.upper().replace(" ", "") if ch in _MRZ_CHARS)


def _mrz_candidates(*texts: str) -> list[str]:
    """Lignes plausibles de MRZ (assez longues, contenant des '<'), dédupliquées."""
    seen: set[str] = set()
    out: list[str] = []
    for text in texts:
        for raw in (text or "").splitlines():
            c = _clean_mrz_line(raw)
            if len(c) >= 25 and c.count("<") >= 1 and c not in seen:
                seen.add(c)
                out.append(c)
    return out


def _pad(line: str, size: int) -> str:
    return (line + "<" * size)[:size]


def _try_checker(kind: str, lines: list[str]):
    """Retourne (valide, champs) ou None."""
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
        return None
    surname = (getattr(f, "surname", "") or "").strip()
    name = (getattr(f, "name", "") or "").strip()
    if not (surname or name):
        return None
    doc = (getattr(f, "document_number", "") or "").replace("<", "").strip()
    try:
        valid = bool(checker)
    except Exception:
        valid = False
    return valid, {
        "nom": surname or None,
        "prenom": name or None,
        "cin": doc or None,
        "nationalite": (getattr(f, "nationality", "") or "").strip() or None,
        "sexe": (getattr(f, "sex", "") or "").strip() or None,
        "date_naissance": _format_birth((getattr(f, "birth_date", "") or "").strip()),
    }


def _parse_mrz(cands: list[str]) -> dict | None:
    """Scanne toutes les fenêtres de lignes consécutives (TD1 = 3, TD3/TD2 = 2)."""
    n = len(cands)
    windows: list[tuple[str, list[str]]] = []
    for i in range(n - 2):
        windows.append(("td1", cands[i:i + 3]))
    for i in range(n - 1):
        windows.append(("td3", cands[i:i + 2]))
        windows.append(("td2", cands[i:i + 2]))

    fallback = None
    for kind, block in windows:
        size = _FMT[kind][1]
        res = _try_checker(kind, [_pad(x, size) for x in block])
        if not res:
            continue
        valid, data = res
        if valid:            # clés de contrôle OK → confiance maximale
            return data
        if fallback is None:  # sinon on garde le 1er lisible en repli
            fallback = data
    return fallback


def _preprocess(image):
    """Niveaux de gris + auto-contraste + mise à l'échelle (aide l'OCR)."""
    from PIL import ImageOps
    g = ImageOps.grayscale(image)
    g = ImageOps.autocontrast(g, cutoff=1)
    w, h = g.size
    m = max(w, h)
    if m < 1600:
        s = 1600 / m
        g = g.resize((int(w * s), int(h * s)))
    elif m > 2600:
        s = 2600 / m
        g = g.resize((int(w * s), int(h * s)))
    return g


def _mrz_passes(image) -> list[str]:
    """OCR MRZ : image entière (psm 6 + 4) + bande du bas (où siège la MRZ).

    Lève l'exception seulement si Tesseract est absent ; sinon ignore la passe.
    """
    cfg6 = f"--psm 6 -c tessedit_char_whitelist={_MRZ_CHARS}"
    cfg4 = f"--psm 4 -c tessedit_char_whitelist={_MRZ_CHARS}"
    w, h = image.size
    bottom = image.crop((0, int(h * 0.55), w, h))  # MRZ en bas (dos CIN / passeport)
    jobs = [(image, cfg6), (image, cfg4), (bottom, cfg6)]
    texts = []
    for img, cfg in jobs:
        try:
            texts.append(_ocr(img, "eng", cfg))
        except Exception as e:
            if _is_tesseract_missing(e):
                raise
    return texts


_CIN_RE = re.compile(r"\b([A-Z]{1,2}\d{4,7})\b")


def _pdf_to_images(data: bytes) -> list:
    """Rend les pages d'un PDF (max 3) en images PIL via PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
    except Exception:
        import pymupdf as fitz  # nom du module selon la version
    from PIL import Image

    out = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in list(doc)[:3]:
            png = page.get_pixmap(dpi=220, alpha=False).tobytes("png")
            out.append(Image.open(io.BytesIO(png)).convert("RGB"))
    return out


def _load_images(data: bytes, filename: str):
    """Retourne (images, erreur). Gère PDF (rendu pages) et images (JPG/PNG…)."""
    is_pdf = filename.lower().endswith(".pdf") or data[:5] == b"%PDF-"
    if is_pdf:
        try:
            imgs = _pdf_to_images(data)
        except Exception:
            return None, _blank_result("PDF illisible — réessayez avec une image (JPG/PNG) nette.")
        if not imgs:
            return None, _blank_result("PDF vide.")
        return imgs, None

    try:
        from PIL import Image
    except Exception:
        return None, _blank_result("Bibliothèque image (Pillow) indisponible sur le serveur.")
    try:
        return [Image.open(io.BytesIO(data)).convert("RGB")], None
    except Exception:
        return None, _blank_result("Image illisible — réessayez avec une photo nette (JPG/PNG). "
                                   "Le format HEIC (iPhone) n'est pas supporté.")


def extract_id_fields(data: bytes, filename: str = "") -> dict:
    """Point d'entrée : bytes (image OU PDF) → champs pré-remplis."""
    if not data:
        return _blank_result("Fichier vide.")

    images, err = _load_images(data, filename)
    if err:
        return err

    # OCR sur chaque page/image : passes MRZ + passe plein texte
    mrz_texts: list[str] = []
    full_texts: list[str] = []
    for image in images:
        prepped = _preprocess(image)
        try:
            mrz_texts.extend(_mrz_passes(prepped))
        except Exception as e:
            if _is_tesseract_missing(e):
                return _blank_result("OCR indisponible : Tesseract n'est pas installé sur le serveur.")
        try:
            full_texts.append(_ocr(image, "fra+eng"))
        except Exception:
            pass

    full_text = "\n".join(full_texts)

    # 1) MRZ — cherchée dans toutes les passes / toutes les pages
    cands = _mrz_candidates(*mrz_texts, full_text)
    parsed = _parse_mrz(cands)
    if parsed:
        return {"ok": True, "source": "mrz", "raw_text": full_text.strip(),
                "message": "Extrait de la zone lisible machine (MRZ). Vérifiez avant de valider.",
                **parsed}

    # 2) Repli : n° CIN par regex
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
        "message": "MRZ non détectée — photographiez la face avec la bande « <<< » "
                   "(dos de la CIN biométrique, ou page photo du passeport). "
                   "N° d'identité déduit du texte ; complétez le nom/prénom si besoin.",
    }
