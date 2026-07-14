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


_CIN_FMT = re.compile(r"^[A-Z]{1,2}\d{5,6}$")  # format CIN marocaine (ex. AB123456)


def _valid_cin(s: str | None) -> str | None:
    """Ne garde le n° que s'il a un format CIN plausible (sinon None, pas de faux)."""
    if not s:
        return None
    s = s.replace("<", "").strip().upper()
    return s if _CIN_FMT.match(s) else None


# Lettres souvent confondues avec le chevron « < » par Tesseract
_CHEVRON_MISREAD = "KLCEUIQ"


def _looks_filler(tok: str) -> bool:
    """Un token « bourrage » (des '<' mal lus, ex. 'LLLLLLLLLE')."""
    if len(tok) < 4:
        return False
    from collections import Counter
    ch, freq = Counter(tok).most_common(1)[0]
    if freq == len(tok):
        return True
    return len(tok) >= 5 and ch in _CHEVRON_MISREAD and freq / len(tok) >= 0.6


def _extract_name_from_line(line: str) -> tuple[str | None, str | None]:
    """Extrait (nom, prénom) d'une ligne MRZ de NOM (TD1 ligne 3), tolérante aux
    '<' mal lus. Ex. 'BABAK<WALID<<<<LLLLLLLLLE' → ('BABAK', 'WALID')."""
    tokens = [t for t in re.split(r"<+", line) if t]
    tokens = [t for t in tokens if not t.isdigit() and not _looks_filler(t)]
    if not tokens:
        return None, None
    surname = tokens[0] or None
    given = " ".join(tokens[1:]) if len(tokens) > 1 else None
    return surname, given


def _best_name_line(cands: list[str]) -> str | None:
    """Ligne de NOM la plus plausible : que des [A-Z<] (pas de chiffres), un '<',
    et qui donne un nom après nettoyage (typique de la ligne 3 d'une CIN TD1)."""
    best = None
    best_len = 0
    for c in cands:
        if any(ch.isdigit() for ch in c) or "<" not in c:
            continue
        sn, _ = _extract_name_from_line(c)
        if sn and len(sn) >= 2 and len(c) > best_len:
            best, best_len = c, len(c)
    return best


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
        "cin": _valid_cin(doc),
        "nationalite": (getattr(f, "nationality", "") or "").strip() or None,
        "sexe": (getattr(f, "sex", "") or "").strip() or None,
        "date_naissance": _format_birth((getattr(f, "birth_date", "") or "").strip()),
    }


def _score(valid: bool, data: dict) -> int:
    s = 100 if valid else 0
    if data.get("nom"):
        s += 15
    if data.get("prenom"):
        s += 15
    if data.get("cin"):
        s += 6
    if data.get("date_naissance"):
        s += 4
    return s


def _parse_mrz(cands: list[str]) -> dict | None:
    """Scanne toutes les fenêtres de lignes consécutives (TD1 = 3, TD3/TD2 = 2)
    et retient le MEILLEUR résultat (clés valides > nom+prénom > n°)."""
    n = len(cands)
    windows: list[tuple[str, list[str]]] = []
    for i in range(n - 2):
        windows.append(("td1", cands[i:i + 3]))
    for i in range(n - 1):
        windows.append(("td3", cands[i:i + 2]))
        windows.append(("td2", cands[i:i + 2]))

    best = None
    best_score = -1
    for kind, block in windows:
        size = _FMT[kind][1]
        res = _try_checker(kind, [_pad(x, size) for x in block])
        if not res:
            continue
        valid, data = res
        sc = _score(valid, data)
        if sc > best_score:
            best_score, best = sc, data
            if valid and data.get("nom") and data.get("prenom"):
                break  # résultat complet et valide : inutile de chercher mieux
    return best


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

    # Complément : extraction directe du nom (tolérante aux '<' mal lus par l'OCR)
    name_line = _best_name_line(cands)
    if name_line:
        sn, gn = _extract_name_from_line(name_line)
        if parsed is None and sn:
            parsed = {"nom": sn, "prenom": gn, "cin": None,
                      "nationalite": None, "sexe": None, "date_naissance": None}
        elif parsed:
            parsed["nom"] = parsed.get("nom") or sn
            parsed["prenom"] = parsed.get("prenom") or gn

    if parsed and (parsed.get("nom") or parsed.get("prenom") or parsed.get("cin")):
        return {"ok": True, "source": "mrz", "raw_text": full_text.strip(),
                "message": "Zone lisible machine (MRZ) lue. Vérifiez et complétez le n° si besoin "
                           "(l'OCR peut se tromper sur quelques caractères).",
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
