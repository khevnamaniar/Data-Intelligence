"""Load everything a project contains into a ProjectData object for analysis."""
from __future__ import annotations

import logging

from ..db import Connection
from ..services.ingestion import load_dataset_frame
from .model import Chunk, Column, DatasetView, DocumentView, ProjectData

log = logging.getLogger("dip.analytics")
STRATEGY_ORDER = {"previous_strategy": 0, "current_strategy": 1, "plan": 1, "proposed_strategy": 2}


def load_project_data(c: Connection, project_id: str, file_ids: list[str] | None = None) -> ProjectData:
    project = c.fetchone("SELECT * FROM projects WHERE id = ?", [project_id])
    files = c.fetchall("SELECT * FROM files WHERE project_id = ? AND status IN ('processed', 'partial') ORDER BY created_at", [project_id])
    if file_ids:
        wanted = set(file_ids)
        files = [f for f in files if f["id"] in wanted]
    fmap = {f["id"]: f for f in files}
    datasets: list[DatasetView] = []
    for ds in c.fetchall("SELECT * FROM datasets WHERE project_id = ? ORDER BY created_at", [project_id]):
        f = fmap.get(ds["file_id"])
        if not f or not ds["normalized_storage_key"]:
            continue
        cols = c.fetchall("SELECT * FROM dataset_columns WHERE dataset_id = ? ORDER BY position", [ds["id"]])
        try:
            df = load_dataset_frame(ds, cols)
        except Exception as exc:  # storage object missing etc. – skip but keep going
            log.warning("Could not load dataset %s: %s", ds["id"], exc)
            continue
        columns = {}
        for col in cols:
            st = col.get("stats_json") or {}
            columns[col["normalized_name"]] = Column(
                name=col["normalized_name"], dtype=col["data_type"], role=col["semantic_role"], unit=col["unit"] or "",
                additive=bool(st.get("additive")), distinct=int(col["distinct_count"] or 0),
                granularity=st.get("granularity") or "", period_group=st.get("period_group") or "")
        profile = ds.get("profile_json") or {}
        datasets.append(DatasetView(
            id=ds["id"], name=ds["name"], file_id=f["id"], file_name=f["original_name"], file_role=f["source_role"],
            period_label=f.get("period_label") or "", origin=ds["origin"], locator=ds.get("locator_json") or {},
            approximate=bool(ds["is_approximate"]), df=df, columns=columns, period_groups=profile.get("period_groups") or {}))
    documents: list[DocumentView] = []
    for i, f in enumerate(files):
        chunks = c.fetchall("SELECT * FROM document_chunks WHERE file_id = ? ORDER BY seq", [f["id"]])
        if not chunks:
            continue
        documents.append(DocumentView(
            file_id=f["id"], name=f["original_name"], role=f["source_role"], period_label=f.get("period_label") or "",
            file_type=f["file_type"], order=i,
            chunks=[Chunk(id=ch["id"], file_id=f["id"], kind=ch["kind"], heading=ch["heading"] or "", text=ch["text"],
                          locator=ch.get("locator_json") or {}, approximate=bool(ch["is_approximate"])) for ch in chunks]))
    issues = c.fetchall("SELECT * FROM data_quality_issues WHERE project_id = ?", [project_id])
    if file_ids:
        issues = [i for i in issues if i["file_id"] in fmap]
    return ProjectData(project=project, datasets=datasets, documents=documents, files=files, quality_issues=issues)
