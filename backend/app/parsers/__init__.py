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
ALIASES = {"markdown": "md", "tsv": "csv"}
IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"}


def safe_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", PurePath(name.replace("\\", "/")).name)
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" ._") or "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem[:150]
    return f"{stem}.{ext.lower()}" if ext else stem


def detect_type(filename: str, data: bytes) -> str:
    """Return the canonical file type, validating that content matches the extension."""
    ext = PurePath(filename).suffix.lower().lstrip(".")
    ext = ALIASES.get(ext, ext)
    if ext not in SUPPORTED:
        raise ParserError(
            f"Files of type '.{ext or '?'}' are not supported.",
            "Supported types: PDF, XLSX, CSV, DOCX, PPTX, TXT, Markdown and images (PNG, JPG, WEBP, GIF, BMP, TIFF).",
            "Convert the file to one of the supported formats.",
        )
    head = data[:16]
    # never accept programs or scripts, whatever their name
    if head.startswith((b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"#!")):
        raise ParserError("This file looks like a program or script and was refused.", "Only documents, spreadsheets and images are accepted.")
    if ext == "pdf" and not data.lstrip()[:5].startswith(b"%PDF"):
        raise ParserError("This file is named .pdf but is not a PDF.", "The content does not match the extension.")
    if ext in ("xlsx", "docx", "pptx"):
        if not head.startswith(b"PK"):
            raise ParserError(f"This file is named .{ext} but is not a valid {ext.upper()} file.",
                              "Older formats (.xls/.doc/.ppt) and password-protected files are not supported.",
                              f"Open it and save as .{ext}.")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = zf.namelist()
            expanded = sum(i.file_size for i in zf.infolist())
        except zipfile.BadZipFile:
            raise ParserError(f"The {ext.upper()} file is corrupt.", "It could not be unzipped.")
        if expanded > 300 * 1024 * 1024 or (len(data) and expanded / len(data) > 200):
            raise ParserError(f"The {ext.upper()} file expands to an unsafe size.", "Its compressed content is abnormally large.",
                              "Re-save the file normally and upload again.")
        if any(n.lower().endswith(("vbaproject.bin", ".exe", ".dll", ".js", ".vbs")) for n in names):
            raise ParserError(f"The {ext.upper()} file contains macros or embedded programs.", "These are not accepted for security reasons.",
                              f"Save a copy without macros (plain .{ext}) and upload that.")
        needed = {"xlsx": "xl/", "docx": "word/", "pptx": "ppt/"}[ext]
        if not any(n.startswith(needed) for n in names):
            raise ParserError(f"This file is named .{ext} but its content is a different kind of file.", "Content and extension do not match.")
    if ext in IMAGE_TYPES:
        sigs = [b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"GIF8", b"BM", b"II*\x00", b"MM\x00*"]
        if not any(head.startswith(s) for s in sigs):
            raise ParserError("This file is not a valid image.", "The content does not match an image format.")
    if ext in ("csv", "txt", "md") and b"\x00" in data[:4096]:
        raise ParserError("This file does not look like text.", "It contains binary data.", "Upload the original text/CSV export.")
    return ext


def parse_file(file_type: str, data: bytes, filename: str, ocr_enabled: bool = True) -> ExtractionResult:
    if file_type == "csv":
        from .tabular import parse_csv

        return parse_csv(data, filename)
    if file_type == "xlsx":
        from .tabular import parse_xlsx

        return parse_xlsx(data, filename)
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
           "safe_filename", "SUPPORTED", "IMAGE_TYPES"]
