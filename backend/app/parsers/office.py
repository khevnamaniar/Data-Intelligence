"""Word (.docx) and PowerPoint (.pptx) parsers."""
from __future__ import annotations

import io
import zipfile

import pandas as pd

from .base import ExtractedChunk, ExtractedTable, ExtractionResult, ParserError, rows_to_table
from .image import analyse_image, load_image, tesseract_available

MAX_IMAGES = 15


def _open_zip_check(data: bytes, required: str, kind: str):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            total = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile:
        raise ParserError(f"The {kind} file could not be opened.", "It is corrupt or not a modern Office file.",
                          f"Open it in the original app and save it again as .{kind.lower()}.")
    if not any(n.startswith(required) for n in names):
        raise ParserError(f"This is not a valid {kind} file.", "The file's internal structure does not match its extension.")
    if total > 500 * 1024 * 1024:
        raise ParserError(f"The {kind} file is too large once decompressed.", "It expands to more than 500 MB.")


# ----------------------------------------------------------------- DOCX
def parse_docx(data: bytes, filename: str, ocr_enabled: bool = True) -> ExtractionResult:
    _open_zip_check(data, "word/", "DOCX")
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    res = ExtractionResult("docx")
    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ParserError("The Word document could not be opened.", f"{exc.__class__.__name__}", "Save it again as .docx and re-upload.")
    heading, section_path = "", []
    para_no = table_no = 0
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            text = " ".join(p.text.split())
            if not text:
                continue
            para_no += 1
            style = (p.style.name if p.style is not None else "") or ""
            loc = {"file": filename, "section": " › ".join(section_path) or "(start)", "paragraph": para_no}
            if style.lower().startswith("heading") or style.lower() == "title":
                try:
                    level = int(style.split()[-1]) if style.lower().startswith("heading") else 0
                except ValueError:
                    level = 1
                section_path = section_path[: max(level - 1, 0)] + [text]
                heading = text
                loc["section"] = " › ".join(section_path)
                res.chunks.append(ExtractedChunk("heading", text, heading, loc))
            elif style.lower().startswith("caption"):
                res.chunks.append(ExtractedChunk("caption", text, heading, loc))
            elif "list" in style.lower() or p._p.pPr is not None and p._p.pPr.numPr is not None:
                res.chunks.append(ExtractedChunk("list_item", text, heading, loc))
            else:
                res.chunks.append(ExtractedChunk("paragraph", text, heading, loc))
        elif tag == "tbl":
            table_no += 1
            t = Table(child, doc)
            rows = []
            for r in t.rows:
                try:
                    rows.append([" ".join(c.text.split()) or None for c in r.cells])
                except Exception:  # pragma: no cover - malformed merged cells
                    continue
            tbl = rows_to_table(rows, f"{filename} › table {table_no}", "docx_table",
                                {"file": filename, "table": table_no, "section": " › ".join(section_path) or "(start)"})
            if tbl is not None:
                res.tables.append(tbl)
    # footnotes are stored separately in the package
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/footnotes.xml" in z.namelist():
                from lxml import etree

                root = etree.fromstring(z.read("word/footnotes.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                for fn in root.findall("w:footnote", ns):
                    if fn.get("{%s}type" % ns["w"]) in ("separator", "continuationSeparator"):
                        continue
                    text = " ".join("".join(t.text or "" for t in fn.iter("{%s}t" % ns["w"])).split())
                    if text:
                        res.chunks.append(ExtractedChunk("footnote", text, "", {"file": filename, "footnote": fn.get("{%s}id" % ns["w"])}))
            images = [n for n in z.namelist() if n.startswith("word/media/")][:MAX_IMAGES]
            for i, name in enumerate(images, start=1):
                try:
                    img = load_image(z.read(name))
                except ParserError:
                    continue
                if img.width < 150 or img.height < 100:
                    continue
                analyse_image(img, res, {"file": filename, "image": i}, f"{filename} › image {i}",
                              ocr_enabled=ocr_enabled and tesseract_available(), min_chars=15)
    except Exception as exc:  # pragma: no cover
        res.warnings.append(f"Footnotes/images could not be read ({exc.__class__.__name__}).")
    res.metadata.update({"paragraphs": para_no, "tables": table_no,
                         "note": "Word files have no fixed pages; sources are referenced by section and paragraph."})
    if not res.chunks and not res.tables:
        res.error("No text or tables were found in the document.", "The document appears to be empty.")
    return res


# ----------------------------------------------------------------- PPTX
def parse_pptx(data: bytes, filename: str, ocr_enabled: bool = True) -> ExtractionResult:
    _open_zip_check(data, "ppt/", "PPTX")
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    res = ExtractionResult("pptx")
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise ParserError("The PowerPoint file could not be opened.", f"{exc.__class__.__name__}", "Save it again as .pptx and re-upload.")
    images_done = 0
    for sno, slide in enumerate(prs.slides, start=1):
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = " ".join(slide.shapes.title.text_frame.text.split())
        loc = {"file": filename, "slide": sno}
        if title:
            res.chunks.append(ExtractedChunk("slide_title", title, title, loc))

        def handle(shape, depth=0):
            nonlocal images_done
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP and depth < 3:
                for s in shape.shapes:
                    handle(s, depth + 1)
                return
            if shape.has_text_frame and (slide.shapes.title is None or shape.shape_id != slide.shapes.title.shape_id):
                for para in shape.text_frame.paragraphs:
                    text = " ".join("".join(r.text for r in para.runs).split())
                    if text:
                        kind = "list_item" if para.level > 0 or len(shape.text_frame.paragraphs) > 1 else "paragraph"
                        res.chunks.append(ExtractedChunk(kind, text, title, dict(loc)))
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[" ".join(c.text.split()) or None for c in r.cells] for r in shape.table.rows]
                t = rows_to_table(rows, f"{filename} › slide {sno} table", "pptx_table", {**loc, "shape": shape.name})
                if t is not None:
                    res.tables.append(t)
            if getattr(shape, "has_chart", False) and shape.has_chart:
                t = _chart_table(shape.chart, f"{filename} › slide {sno} chart", {**loc, "shape": shape.name})
                if t is not None:
                    res.tables.append(t)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and images_done < MAX_IMAGES:
                try:
                    img = load_image(shape.image.blob)
                except Exception:
                    return
                if img.width >= 150 and img.height >= 100:
                    images_done += 1
                    analyse_image(img, res, {**loc, "image": shape.name}, f"{filename} › slide {sno} image",
                                  ocr_enabled=ocr_enabled and tesseract_available(), min_chars=15)

        for shape in slide.shapes:
            try:
                handle(shape)
            except Exception as exc:  # pragma: no cover
                res.warnings.append(f"A shape on slide {sno} could not be read ({exc.__class__.__name__}).")
        if slide.has_notes_slide:
            notes = " ".join(slide.notes_slide.notes_text_frame.text.split()) if slide.notes_slide.notes_text_frame else ""
            if notes:
                res.chunks.append(ExtractedChunk("note", notes, title, {**loc, "part": "speaker notes"}))
    res.metadata["slides"] = len(prs.slides)
    if not res.chunks and not res.tables:
        res.error("No text, tables or charts were found in the presentation.", "The slides appear to be empty or image-only.")
    return res


def _chart_table(chart, name: str, loc: dict) -> ExtractedTable | None:
    """Native PowerPoint charts store their data exactly — extract it."""
    try:
        plot = chart.plots[0]
        cats = [str(c) for c in plot.categories]
        data = {"Category": cats}
        for s in plot.series:
            vals = list(s.values)
            if len(vals) == len(cats):
                data[s.name or f"Series {len(data)}"] = vals
        if len(data) < 2 or not cats:
            return None
        df = pd.DataFrame(data)
        title = chart.chart_title.text_frame.text if chart.has_title and chart.chart_title.has_text_frame else ""
        return ExtractedTable(name=name + (f" – {title}" if title else ""), df=df, origin="pptx_chart",
                              locator={**loc, "chart_title": title}, row_numbers=list(range(1, len(df) + 1)),
                              note="Exact values read from the chart's embedded data")
    except Exception:
        return None
