"""Format detection by signature first, then Content-Type, then extension.

A file named ``requerimientos.pdf`` that actually holds a ZIP must never be
parsed as a PDF, so signature evidence always wins and disagreements are kept
as warnings rather than silently resolved.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    FORMAT_CSV,
    FORMAT_DOC,
    FORMAT_DOCX,
    FORMAT_IMAGE,
    FORMAT_LEGACY_OLE,
    FORMAT_PDF,
    FORMAT_PPTX,
    FORMAT_RAR,
    FORMAT_SEVENZIP,
    FORMAT_TXT,
    FORMAT_UNKNOWN,
    FORMAT_XLS,
    FORMAT_XLSX,
    FORMAT_XML,
    FORMAT_ZIP,
    ROLE_ADMINISTRATIVE_BASIS,
    ROLE_ECONOMIC_FORM,
    ROLE_TECHNICAL_SPEC,
    ROLE_TEMPLATE,
    ROLE_UNKNOWN,
)

_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
)

_EXTENSION_FORMATS: dict[str, str] = {
    "pdf": FORMAT_PDF,
    "docx": FORMAT_DOCX,
    "docm": FORMAT_DOCX,
    "xlsx": FORMAT_XLSX,
    "xlsm": FORMAT_XLSX,
    "pptx": FORMAT_PPTX,
    "zip": FORMAT_ZIP,
    "doc": FORMAT_DOC,
    "xls": FORMAT_XLS,
    "csv": FORMAT_CSV,
    "txt": FORMAT_TXT,
    "xml": FORMAT_XML,
    "rar": FORMAT_RAR,
    "7z": FORMAT_SEVENZIP,
    "png": FORMAT_IMAGE,
    "jpg": FORMAT_IMAGE,
    "jpeg": FORMAT_IMAGE,
    "gif": FORMAT_IMAGE,
    "bmp": FORMAT_IMAGE,
    "tif": FORMAT_IMAGE,
    "tiff": FORMAT_IMAGE,
}

_CONTENT_TYPE_FORMATS: tuple[tuple[str, str], ...] = (
    ("application/pdf", FORMAT_PDF),
    ("wordprocessingml.document", FORMAT_DOCX),
    ("spreadsheetml.sheet", FORMAT_XLSX),
    ("presentationml.presentation", FORMAT_PPTX),
    ("application/zip", FORMAT_ZIP),
    ("application/msword", FORMAT_DOC),
    ("application/vnd.ms-excel", FORMAT_XLS),
    ("text/csv", FORMAT_CSV),
    ("text/xml", FORMAT_XML),
    ("application/xml", FORMAT_XML),
    ("text/plain", FORMAT_TXT),
    ("image/", FORMAT_IMAGE),
    ("application/x-rar", FORMAT_RAR),
    ("application/x-7z", FORMAT_SEVENZIP),
)


@dataclass(frozen=True)
class FormatDetection:
    """What the file actually is, plus how confidently we decided."""

    detected_format: str
    evidence: str
    extension: str
    content_type_format: str | None
    extension_format: str | None
    warnings: tuple[str, ...] = ()


def extension_of(filename: str) -> str:
    name = (filename or "").strip().lower()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1]


def _content_type_format(content_type: str) -> str | None:
    ct = (content_type or "").strip().lower()
    if not ct:
        return None
    for needle, fmt in _CONTENT_TYPE_FORMATS:
        if needle in ct:
            return fmt
    return None


def classify_zip_container(payload: bytes) -> tuple[str, tuple[str, ...]]:
    """Decide whether a PK container is OOXML or a generic ZIP by its entries."""
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return FORMAT_ZIP, ("zip_container_unreadable",)
    except Exception:  # pragma: no cover - defensive
        return FORMAT_ZIP, ("zip_container_unreadable",)
    lowered = [n.lower() for n in names]
    if any(n.startswith("word/") for n in lowered):
        return FORMAT_DOCX, tuple(warnings)
    if any(n.startswith("xl/") for n in lowered):
        return FORMAT_XLSX, tuple(warnings)
    if any(n.startswith("ppt/") for n in lowered):
        return FORMAT_PPTX, tuple(warnings)
    return FORMAT_ZIP, tuple(warnings)


def _looks_like_text(payload: bytes) -> bool:
    sample = payload[:4096]
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    control = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return control / len(sample) < 0.05


def _text_subformat(payload: bytes, extension_format: str | None) -> str:
    stripped = payload[:512].lstrip()
    if stripped.startswith(b"\xef\xbb\xbf"):
        stripped = stripped[3:].lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        return FORMAT_XML
    if extension_format in {FORMAT_CSV, FORMAT_TXT, FORMAT_XML}:
        return extension_format
    return FORMAT_TXT


def detect_format(
    payload: bytes,
    *,
    content_type: str = "",
    filename: str = "",
) -> FormatDetection:
    """Signature > Content-Type > extension, recording every disagreement."""
    extension = extension_of(filename)
    ext_fmt = _EXTENSION_FORMATS.get(extension)
    ct_fmt = _content_type_format(content_type)
    warnings: list[str] = []

    detected: str
    evidence: str
    if payload.startswith(b"%PDF"):
        detected, evidence = FORMAT_PDF, "signature"
    elif any(payload.startswith(sig) for sig in _ZIP_SIGNATURES):
        detected, container_warnings = classify_zip_container(payload)
        evidence = "zip_container"
        warnings.extend(container_warnings)
    elif payload.startswith(_OLE_SIGNATURE):
        # Distinguishing .doc from .xls inside OLE needs the directory stream;
        # fall back to the declared type and keep it explicit.
        if ext_fmt in {FORMAT_XLS, FORMAT_DOC}:
            detected = ext_fmt
        elif ct_fmt in {FORMAT_XLS, FORMAT_DOC}:
            detected = ct_fmt
        else:
            detected = FORMAT_LEGACY_OLE
        evidence = "signature"
    elif payload.startswith(b"Rar!\x1a\x07"):
        detected, evidence = FORMAT_RAR, "signature"
    elif payload.startswith(b"7z\xbc\xaf\x27\x1c"):
        detected, evidence = FORMAT_SEVENZIP, "signature"
    elif any(payload.startswith(sig) for sig, _ in _IMAGE_SIGNATURES):
        detected, evidence = FORMAT_IMAGE, "signature"
    elif _looks_like_text(payload):
        detected = _text_subformat(payload, ext_fmt)
        evidence = "text_heuristic"
    elif ct_fmt is not None:
        detected, evidence = ct_fmt, "content_type"
    elif ext_fmt is not None:
        detected, evidence = ext_fmt, "extension"
    else:
        detected, evidence = FORMAT_UNKNOWN, "unknown"

    if ext_fmt is not None and ext_fmt != detected and evidence != "extension":
        warnings.append(f"extension_mismatch:{extension or 'none'}->{detected}")
    if ct_fmt is not None and ct_fmt != detected and evidence != "content_type":
        warnings.append(f"content_type_mismatch:{ct_fmt}->{detected}")

    return FormatDetection(
        detected_format=detected,
        evidence=evidence,
        extension=extension,
        content_type_format=ct_fmt,
        extension_format=ext_fmt,
        warnings=tuple(warnings),
    )


_ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        ROLE_TECHNICAL_SPEC,
        ("tecnic", "técnic", "especificac", "requerimient", "ficha", "spec"),
    ),
    (
        ROLE_ADMINISTRATIVE_BASIS,
        ("administrativ", "bases", "adminis"),
    ),
    (
        ROLE_ECONOMIC_FORM,
        ("economic", "económic", "oferta", "precio", "presupuest"),
    ),
    (
        ROLE_TEMPLATE,
        ("formulario", "formato", "plantilla", "anexo n"),
    ),
)


def role_tag_for(filename: str, tipo: str = "", descripcion: str = "") -> str:
    """Non-authoritative hint only; a role tag must never cause a file to be skipped."""
    blob = f"{filename} {tipo} {descripcion}".lower()
    for role, needles in _ROLE_KEYWORDS:
        if any(needle in blob for needle in needles):
            return role
    return ROLE_UNKNOWN
