"""PDF parser: text with headings/captions/footnotes, tables, scanned pages (OCR) and chart images."""
from __future__ import annotations

import io
import logging
import re
import statistics

from . import pdf_layout as layout
from .base import ExtractedChunk, ExtractionResult, ParserError, rows_to_table
from .image import analyse_image, tesseract_available

log = logging.getLogger("dip.parsers")

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
    try:
        period = layout.detect_report_period([(pg.extract_text() or "")[:600] for pg in pdf.pages[:40]])
    except Exception:  # pragma: no cover
        period = None
    if period:
        res.metadata["report_period"] = period
    year = int(period["start"][:4]) if period else None
    blank_pages = []
    for pno, page in enumerate(pdf.pages, start=1):
        try:
            lines = _lines_with_size(page)
        except Exception as exc:
            res.error(f"Page {pno} could not be read.", str(exc)[:200], "Other pages were still analysed.")
            continue
        page_area = float(page.width or 600) * float(page.height or 800)
        if len("".join(t for t, _, _ in lines)) < 20:
            # Only pages that are mostly a picture are "scanned"; cover/closing pages ("Thank You") are not.
            img_area = sum(max(0.0, float(im["x1"] - im["x0"])) * max(0.0, float(im["bottom"] - im["top"])) for im in page.images)
            if img_area >= 0.35 * page_area:
                scanned_pages.append(pno)
            else:
                blank_pages.append(pno)
                for t, _, _ in lines:
                    res.chunks.append(ExtractedChunk("note", t, current_heading, {"file": filename, "page": pno}))
            continue
        sizes = [s for _, s, _ in lines if s]
        body_size = statistics.median(sizes) if sizes else 10
        page_height = float(page.height or 800)
        used: list[tuple] = []  # regions whose text became a table/chart (not repeated as paragraphs)
        table_tops: list[tuple[float, float]] = []

        def add_table(rows, title, kind, region, approximate=False, note=""):
            nonlocal_ti[0] += 1
            name = f"{title} ({filename} › page {pno})" if title else f"{filename} › page {pno} {kind} {nonlocal_ti[0]}"
            t = rows_to_table(rows, name, "pdf_chart" if "chart" in kind else "pdf_table",
                              {"file": filename, "page": pno, "table": nonlocal_ti[0], "kind": kind, "title": title},
                              first_row_number=1, approximate=approximate)
            if t is None or t.df.shape[1] < 2:
                return False
            t.note = note
            res.tables.append(t)
            used.append(region)
            return True

        nonlocal_ti = [0]
        try:
            words = page.extract_words(extra_attrs=["size"], keep_blank_chars=False)
            wlines = layout.group_lines(words)
            found = page.find_tables()
        except Exception as exc:  # pragma: no cover
            res.warnings.append(f"The layout of page {pno} could not be analysed ({exc.__class__.__name__}); its text was still read.")
            words, wlines, found = [], [], []
        # 1. KPI tiles ("Clicks 157")
        for k in layout.kpi_tiles(found, page_area):
            title = k["title"] or layout._title_above(wlines, k["bbox"][1], k["bbox"][0], k["bbox"][2], body_size) or "Key figures"
            add_table([list(k["values"].keys()), list(k["values"].values())], title, "key figures", k["bbox"])
        # 2. charts drawn as vector shapes
        for finder in (lambda: layout.line_charts(page, words, wlines, body_size, year),
                       lambda: layout.bar_charts(page, words, wlines, body_size),
                       lambda: layout.pie_charts(page, words, wlines, body_size)):
            try:
                charts = finder()
            except Exception as exc:  # pragma: no cover - never fail a file because of a chart
                log.warning("Chart reading failed on page %s: %s", pno, exc.__class__.__name__)
                charts = []
            for ch in charts:
                if any(layout._overlaps(ch["region"], u) for u in used):
                    continue
                note = ch.get("note") or (f"Read from the {ch['kind']}'s printed values."
                                          + (" Some values had no printed label and were estimated from the shape's size."
                                             if ch["approximate"] else ""))
                add_table(ch["rows"], ch["title"], ch["kind"], ch["region"], ch["approximate"], note)
        # 3. tables with ruling lines (skip page frames / layout boxes)
        try:
            for tbl in found:
                if any(layout._overlaps(tbl.bbox, u, pad=-3) for u in used):
                    continue
                x0, t0, x1, b0 = tbl.bbox
                if (x1 - x0) * (b0 - t0) > 0.6 * page_area:
                    continue
                rows = tbl.extract()
                cells = [str(c) for r in rows for c in r if c and str(c).strip()]
                if not cells or max(str(c).count("\n") for c in cells) > 6:
                    continue
                if sum(1 for r in rows if sum(1 for c in r if c and str(c).strip()) >= 2) < 2:
                    continue  # one cell per row = text in a box, not a table
                if add_table(rows, layout._title_above(wlines, t0, x0, x1, body_size), "table", tbl.bbox):
                    table_tops.append((t0, b0))
        except Exception as exc:  # pragma: no cover
            res.warnings.append(f"Tables on page {pno} could not be extracted ({exc.__class__.__name__}); its text was still read.")
        # 4. tables without ruling lines (aligned columns)
        free = [ln for ln in wlines if not any(layout._overlaps((ln.words[0]["x0"], ln.top, ln.words[-1]["x1"], ln.bottom), u, pad=-1)
                                               for u in used)]
        for bt in layout.borderless_tables(free, body_size):
            title = layout._title_above(wlines, bt["top"], bt["region"][0], bt["region"][2], body_size)
            add_table(bt["rows"], title, "table", bt["region"])
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
            if any(u[1] <= top + size * 0.5 <= u[3] for u in used):
                continue
            is_heading = size >= body_size * 1.18 and len(text) < 120 and not text.endswith(".")
            if is_heading and layout.is_num(text.replace(" ", "")):
                is_heading = False
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
        # images that may be charts or infographics (logos and icons are skipped by size)
        if images_done < MAX_IMAGES and page.images:
            for ii, im in enumerate(page.images, start=1):
                if images_done >= MAX_IMAGES:
                    break
                wpt, hpt = float(im["x1"] - im["x0"]), float(im["bottom"] - im["top"])
                if wpt < 120 or hpt < 80:
                    continue
                if any(layout._overlaps((im["x0"], im["top"], im["x1"], im["bottom"]), u, pad=-5) for u in used):
                    continue
                img = _render_region(data, pno - 1, (im["x0"], im["top"], im["x1"], im["bottom"]))
                if img is None:
                    continue
                images_done += 1
                analyse_image(img, res, {"file": filename, "page": pno, "image": ii}, f"{filename} › page {pno} image {ii}",
                              ocr_enabled=ocr_enabled and tesseract_available(), min_chars=15)
    if blank_pages:
        res.metadata["pages_without_data"] = blank_pages
    _complete_partial_listings(res)
    if period:
        _stamp_period(res, period)
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
            pages = ", ".join(str(p) for p in scanned_pages[:10]) + ("…" if len(scanned_pages) > 10 else "")
            res.error(f"Page(s) {pages} are pictures of text (for example a scan), so their words could not be read.",
                      "Reading text from pictures (OCR) is not available on this server.",
                      "All other pages were read and analysed.",
                      "If those pages matter, upload a version exported directly from the original app instead of a scan.")
    return res


TIME_COL_RE = re.compile(r"\b(date|day|week|month|quarter|year|period|time)\b", re.I)


RATE_NAME_RE = re.compile(r"%|\b(avg|average|rate|ratio|ctr|cpc|cpm|cpa|cpl|roas|roi|per|share|position|score|rank)\b", re.I)


def _key(name) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def _complete_partial_listings(res: ExtractionResult) -> None:
    """Dashboards often list only the top rows ("top 10 keywords"). When the report's own key figures show a
    larger total, add one '(other — not listed in the report)' row so totals, shares and trends stay correct."""
    kpis: dict[str, tuple[float, float]] = {}  # name -> (value, rounding tolerance of the printed figure)
    for t in res.tables:
        if t.locator.get("kind") == "key figures" and len(t.df) == 1:
            for col in t.df.columns:
                raw = str(t.df[col].iloc[0]).strip()
                v = layout.to_float(raw)
                if v is None or RATE_NAME_RE.search(str(col)):
                    continue
                mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(raw[-1:].lower(), 1.0)
                decimals = len(raw.rstrip("kKmMbB%").split(".")[1]) if "." in raw else 0
                kpis[_key(col)] = (v, 0.5 * mult * 10 ** -decimals + 0.01)
    if not kpis:
        return
    for t in res.tables:
        if t.locator.get("kind") in ("key figures", "line chart") or len(t.df) < 2:
            continue
        dims = [c for c in t.df.columns if not any(layout.is_num(str(v)) for v in t.df[c].dropna().head(20) if str(v).strip())]
        if not dims:
            continue
        extra, matched = {}, 0
        for col in t.df.columns:
            k = _key(col)
            if k not in kpis or RATE_NAME_RE.search(str(col)):
                continue
            vals = [layout.to_float(str(v)) for v in t.df[col] if str(v).strip()]
            if not vals or any(v is None for v in vals):
                continue
            matched += 1
            listed, (total, tol) = sum(vals), kpis[k]
            if total - listed > tol:
                extra[col] = round(total - listed, 2)
            elif listed - total > max(tol, 0.02 * total):  # the table is not a breakdown of the key figure
                extra = {}
                matched = 0
                break
        if not extra or matched == 0:
            continue
        row = {c: "" for c in t.df.columns}
        row[dims[0]] = "(other — not listed in the report)"
        for c in dims[1:]:
            row[c] = "(not listed)"
        for c, v in extra.items():
            row[c] = f"{v:g}"
        t.df.loc[len(t.df)] = row
        if t.row_numbers is not None:
            t.row_numbers = list(t.row_numbers) + [max([n for n in t.row_numbers if n is not None] or [0]) + 1]
        t.note = ((t.note + " ") if t.note else "") + (
            "The report lists only its top rows; one '(other — not listed in the report)' row was added so totals match the "
            "report's key figures (" + ", ".join(f"{c}: {v:g}" for c, v in extra.items()) + ").")
        t.locator["completed_from_key_figures"] = True


def _stamp_period(res: ExtractionResult, period: dict) -> None:
    """Add a 'Period' column so monthly/quarterly reports of the same kind line up into one time series."""
    for t in res.tables:
        if any(TIME_COL_RE.search(str(c)) for c in t.df.columns):
            continue
        t.df.insert(0, "Period", period["value"])
        t.locator["period"] = period["label"]


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
