"""PDF parser: text with headings/captions/footnotes, tables, scanned pages (OCR) and chart images."""
from __future__ import annotations

import io
import re
import statistics

from .base import ExtractedChunk, ExtractionResult, ParserError, rows_to_table
from .image import analyse_image, tesseract_available

CAPTION_RE = re.compile(r"^(figure|fig\.|table|chart|exhibit|graph|source[s]?:|note[s]?:)\s*", re.I)
FOOTNOTE_RE = re.compile(r"^(\d{1,2}|\*{1,3}|†|‡)\s+\S")
MAX_IMAGES = 12


def _lines_with_size(page) -> list[tuple[str, float, float]]:
    """Group characters into lines; return (text, max_font_size, top)."""
    lines: dict[int, list] = {}
    for ch in page.chars:
        key = round(ch["top"] / 3)
        lines.setdefault(key, []).append(ch)
    out = []
    for key in sorted(lines):
        chars = sorted(lines[key], key=lambda c: c["x0"])
        text, prev = "", None
        for c in chars:
            if prev is not None and c["x0"] - prev["x1"] > max(1.0, 0.25 * c.get("size", 10)):
                text += " "
            text += c["text"]
            prev = c
        text = " ".join(text.split())
        if text:
            out.append((text, max(c.get("size", 0) for c in chars), min(c["top"] for c in chars)))
    return out


def parse_pdf(data: bytes, filename: str, ocr_enabled: bool = True) -> ExtractionResult:
    res = ExtractionResult("pdf")
    try:
        import pdfplumber

        pdf = pdfplumber.open(io.BytesIO(data))
        n_pages = len(pdf.pages)
    except Exception as exc:
        msg = str(exc).lower()
        why = "The PDF is password-protected." if "password" in msg or "encrypt" in msg else f"The file is not a readable PDF ({exc.__class__.__name__})."
        raise ParserError("The PDF could not be opened.", why, "Remove the password or export a fresh PDF and upload again.")
    res.metadata["pages"] = n_pages
    scanned_pages, images_done = [], 0
    current_heading = ""
    for pno, page in enumerate(pdf.pages, start=1):
        try:
            lines = _lines_with_size(page)
        except Exception as exc:
            res.error(f"Page {pno} could not be read.", str(exc)[:200], "Other pages were still analysed.")
            continue
        if len("".join(t for t, _, _ in lines)) < 20:
            scanned_pages.append(pno)
            continue
        sizes = [s for _, s, _ in lines if s]
        body_size = statistics.median(sizes) if sizes else 10
        page_height = float(page.height or 800)
        # tables first so that their text is not duplicated as paragraphs
        table_tops: list[tuple[float, float]] = []
        try:
            for ti, tbl in enumerate(page.find_tables(), start=1):
                rows = tbl.extract()
                t = rows_to_table(rows, f"{filename} › page {pno} table {ti}", "pdf_table",
                                  {"file": filename, "page": pno, "table": ti}, first_row_number=1)
                if t is not None and t.df.shape[0] >= 1 and t.df.shape[1] >= 2:
                    res.tables.append(t)
                    table_tops.append((tbl.bbox[1], tbl.bbox[3]))
        except Exception as exc:  # pragma: no cover
            res.warnings.append(f"Tables on page {pno} could not be extracted ({exc.__class__.__name__}); its text was still read.")
        para: list[str] = []
        para_idx = 0

        def flush():
            nonlocal para, para_idx
            if para:
                para_idx += 1
                res.chunks.append(ExtractedChunk("paragraph", " ".join(para), current_heading,
                                                 {"file": filename, "page": pno, "paragraph": para_idx}))
                para = []

        prev_top = None
        for text, size, top in lines:
            if any(a - 2 <= top <= b + 2 for a, b in table_tops):
                continue
            is_heading = size >= body_size * 1.18 and len(text) < 120 and not text.endswith(".")
            if is_heading:
                flush()
                current_heading = text
                res.chunks.append(ExtractedChunk("heading", text, text, {"file": filename, "page": pno}))
            elif CAPTION_RE.match(text):
                flush()
                res.chunks.append(ExtractedChunk("caption", text, current_heading, {"file": filename, "page": pno}))
            elif FOOTNOTE_RE.match(text) and top > page_height * 0.82 and size <= body_size * 0.95:
                flush()
                res.chunks.append(ExtractedChunk("footnote", text, current_heading, {"file": filename, "page": pno}))
            elif text.lstrip().startswith(("•", "-", "–", "▪", "●")):
                flush()
                res.chunks.append(ExtractedChunk("list_item", text.lstrip("•-–▪● ").strip(), current_heading,
                                                 {"file": filename, "page": pno}))
            else:
                if prev_top is not None and top - prev_top > size * 1.9:
                    flush()
                para.append(text)
            prev_top = top
        flush()
        # images that may be charts or infographics
        if images_done < MAX_IMAGES and page.images:
            for ii, im in enumerate(page.images, start=1):
                if images_done >= MAX_IMAGES:
                    break
                wpt, hpt = float(im["x1"] - im["x0"]), float(im["bottom"] - im["top"])
                if wpt < 120 or hpt < 80:
                    continue
                img = _render_region(data, pno - 1, (im["x0"], im["top"], im["x1"], im["bottom"]))
                if img is None:
                    continue
                images_done += 1
                analyse_image(img, res, {"file": filename, "page": pno, "image": ii}, f"{filename} › page {pno} image {ii}",
                              ocr_enabled=ocr_enabled and tesseract_available(), min_chars=15)
    pdf.close()
    if scanned_pages:
        if ocr_enabled and tesseract_available():
            for pno in scanned_pages[:60]:
                img = _render_page(data, pno - 1)
                if img is not None:
                    analyse_image(img, res, {"file": filename, "page": pno, "scanned": True}, f"{filename} › page {pno}",
                                  ocr_enabled=True)
            res.metadata["ocr_pages"] = scanned_pages[:60]
            if len(scanned_pages) > 60:
                res.warnings.append(f"Only the first 60 of {len(scanned_pages)} scanned pages were OCR'd.")
        else:
            res.error(f"{len(scanned_pages)} page(s) look scanned and contain no selectable text.",
                      "OCR is turned off or Tesseract is not installed.",
                      "The other pages were analysed.", "Install Tesseract or enable OCR, then click Re-process.")
    return res


def _render_page(data: bytes, index: int, scale: float = 2.5):
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(data)
        page = doc[index]
        img = page.render(scale=scale).to_pil().convert("RGB")
        doc.close()
        return img
    except Exception:
        return None


def _render_region(data: bytes, index: int, bbox, scale: float = 3.0):
    img = _render_page(data, index, scale=scale)
    if img is None:
        return None
    x0, top, x1, bottom = [float(v) * scale for v in bbox]
    try:
        return img.crop((max(0, x0), max(0, top), min(img.width, x1), min(img.height, bottom)))
    except Exception:
        return None


def ensure_pdf(data: bytes) -> None:
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise ParserError("This is not a valid PDF file.", "The file content does not start with a PDF signature.")
