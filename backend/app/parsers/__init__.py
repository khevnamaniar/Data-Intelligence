"""File type detection, validation and parser dispatch."""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import PurePath

from .base import ExtractedChunk, ExtractedTable, ExtractionResult, ParserError

SUPPORTED = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
    "xls": "application/vnd.ms-excel",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "json": "application/json",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "md": "text/markdown",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}
ALIASES = {"markdown": "md", "tsv": "csv", "tab": "csv", "dat": "csv", "jsonl": "json", "ndjson": "json", "xltx": "xlsx"}
SPREADSHEETS = {"xlsx", "xlsm", "xlsb", "xls", "ods"}
TABULAR = SPREADSHEETS | {"csv", "json"}
IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"}


def safe_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", PurePath(name.replace("\\", "/")).name)
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" ._") or "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem[:150]
    return f"{stem}.{ext.lower()}" if ext else stem


def _sniff(data: bytes) -> str | None:
    """What the content really is, whatever the file is called."""
    head = data[:16]
    if head.startswith(b"PK"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = zf.namelist()
        except zipfile.BadZipFile:
            return None
        if "mimetype" in names:
            mt = zf.read("mimetype")[:100]
            if b"opendocument.spreadsheet" in mt:
                return "ods"
        if any(n.startswith("xl/") for n in names):
            if "xl/workbook.bin" in names:
                return "xlsb"
            return "xlsm" if "xl/vbaProject.bin" in names else "xlsx"
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("ppt/") for n in names):
            return "pptx"
        return None
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"  # legacy Office (.xls/.doc) or a password-protected modern file
    if data[:5].lstrip().startswith(b"%PDF") or data.lstrip()[:5].startswith(b"%PDF"):
        return "pdf"
    return None


def _is_text(data: bytes) -> bool:
    if data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    sample = data[:4096]
    if not sample:
        return True
    zeros = sample.count(0)
    if zeros and sample[1::2].count(0) > 0.4 * len(sample[1::2]) and sample[0::2].count(0) < 0.1 * len(sample[0::2]):  # UTF-16, no BOM
        return True
    return zeros == 0


def detect_type(filename: str, data: bytes) -> str:
    """Return the canonical file type, judged by the content (a CSV called .xlsx is read as CSV, and so on)."""
    ext = PurePath(filename).suffix.lower().lstrip(".")
    ext = ALIASES.get(ext, ext)
    if ext not in SUPPORTED:
        raise ParserError(
            f"Files of type '.{ext or '?'}' are not supported.",
            "Supported types: Excel (XLSX, XLSM, XLSB, XLS), OpenDocument (ODS), CSV/TSV/TXT, JSON, PDF, Word (DOCX), "
            "PowerPoint (PPTX), Markdown and images (PNG, JPG, WEBP, GIF, BMP, TIFF).",
            "Convert the file to one of the supported formats.",
        )
    head = data[:16]
    # never accept programs or scripts, whatever their name
    if head.startswith((b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"#!")):
        raise ParserError("This file looks like a program or script and was refused.", "Only documents, spreadsheets and images are accepted.")
    if not data:
        if ext in ("csv", "txt", "md", "json"):
            return ext
        raise ParserError("The file is empty.", "It has no content.", "Upload the file again.")
    real = _sniff(data)
    if real in ("xlsx", "xlsm", "xlsb", "ods", "docx", "pptx"):
        _check_zip(data, real)
        if real in SPREADSHEETS and (ext in TABULAR or ext == "txt"):
            return real
        if real == ext:
            return real
        raise ParserError(f"This file is named .{ext} but is really a {real.upper()} file.", "Content and extension do not match.",
                          f"Rename it to .{real} and upload again.")
    if real == "ole":
        if b"EncryptedPackage" in data[:200_000] or "EncryptedPackage".encode("utf-16-le") in data[:200_000]:
            raise ParserError("The file is password-protected.", "Its contents are encrypted, so they cannot be read without the password.",
                              "Open it, remove the password (File › Info › Protect), save and upload again.")
        if ext in TABULAR or ext == "txt":
            return "xls"
        raise ParserError(f"This file is in an old Office format (.{'doc' if ext == 'docx' else 'ppt' if ext == 'pptx' else ext}).",
                          "Old Word/PowerPoint formats are not supported.", f"Open it and save as .{ext}.")
    if real == "pdf":
        if ext != "pdf":
            raise ParserError(f"This file is named .{ext} but is really a PDF.", "Content and extension do not match.", "Rename it to .pdf.")
        return "pdf"
    if ext == "pdf":
        raise ParserError("This file is named .pdf but is not a PDF.", "The content does not match the extension.")
    if ext in SPREADSHEETS:
        if _is_text(data):  # e.g. a CSV export saved with an .xls/.xlsx name
            return "json" if data.lstrip()[:1] in (b"{", b"[") else "csv"
        raise ParserError(f"This file is named .{ext} but is not a valid spreadsheet.", "It may be corrupt or in an unusual format.",
                          f"Open it and save it again as .{ext}.")
    if ext in ("docx", "pptx"):
        raise ParserError(f"This file is named .{ext} but is not a valid {ext.upper()} file.",
                          "Older formats (.doc/.ppt) and password-protected files are not supported.", f"Open it and save as .{ext}.")
    if ext in IMAGE_TYPES:
        sigs = [b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"GIF8", b"BM", b"II*\x00", b"MM\x00*"]
        if not any(head.startswith(s) for s in sigs):
            raise ParserError("This file is not a valid image.", "The content does not match an image format.")
        return ext
    if ext in ("csv", "txt", "md", "json"):
        if not _is_text(data):
            raise ParserError("This file does not look like text.", "It contains binary data.", "Upload the original text/CSV export.")
        if ext == "csv" and data.lstrip(b"\xef\xbb\xbf \r\n\t")[:1] in (b"{", b"[") and data.rstrip()[-1:] in (b"}", b"]"):
            return "json"
    return ext


def _check_zip(data: bytes, kind: str) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        expanded = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile:
        raise ParserError(f"The {kind.upper()} file is corrupt.", "It could not be unzipped.")
    if expanded > 400 * 1024 * 1024 or (len(data) and expanded / len(data) > 200):
        raise ParserError(f"The {kind.upper()} file expands to an unsafe size.", "Its compressed content is abnormally large.",
                          "Re-save the file normally and upload again.")
    if any(n.lower().endswith((".exe", ".dll", ".js", ".vbs", ".ps1", ".bat")) for n in names):
        raise ParserError(f"The {kind.upper()} file contains embedded programs.", "These are not accepted for security reasons.",
                          "Save a plain copy of the file and upload that.")
    if kind in ("docx", "pptx") and any(n.lower().endswith("vbaproject.bin") for n in names):
        raise ParserError(f"The {kind.upper()} file contains macros.", "These are not accepted for security reasons.",
                          f"Save a copy without macros (plain .{kind}) and upload that.")


def parse_file(file_type: str, data: bytes, filename: str, ocr_enabled: bool = True) -> ExtractionResult:
    if file_type == "csv":
        from .tabular import parse_csv

        return parse_csv(data, filename)
    if file_type in SPREADSHEETS:
        from .tabular import parse_spreadsheet

        return parse_spreadsheet(data, filename, file_type)
    if file_type == "json":
        from .tabular import parse_json

        return parse_json(data, filename)
    if file_type == "txt":
        from .tabular import _decode, looks_delimited, parse_csv

        if looks_delimited(_decode(data[:200_000])[0]):
            return parse_csv(data, filename)
    if file_type == "pdf":
        from .pdf import parse_pdf

        return parse_pdf(data, filename, ocr_enabled=ocr_enabled)
    if file_type == "docx":
        from .office import parse_docx

        return parse_docx(data, filename, ocr_enabled=ocr_enabled)
    if file_type == "pptx":
        from .office import parse_pptx

        return parse_pptx(data, filename, ocr_enabled=ocr_enabled)
    if file_type in ("txt", "md"):
        from .text import parse_text

        return parse_text(data, filename, markdown=file_type == "md")
    if file_type in IMAGE_TYPES:
        from .image import parse_image

        return parse_image(data, filename, ocr_enabled=ocr_enabled)
    raise ParserError(f"No parser for '{file_type}'.", "Unsupported type.")


__all__ = ["ExtractionResult", "ExtractedTable", "ExtractedChunk", "ParserError", "detect_type", "parse_file",
           "safe_filename", "SUPPORTED", "IMAGE_TYPES", "SPREADSHEETS", "TABULAR"]
