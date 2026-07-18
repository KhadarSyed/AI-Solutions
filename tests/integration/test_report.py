"""Report builder produces a valid branded .docx from cached data."""

import io
import zipfile

from docx import Document

from app.agents.report_builder import _template_for, build_report
from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.db.base import get_sessionmaker
from app.db.models import Project
from app.db.models import Session as SessionRow


def test_template_selection_by_brand():
    assert _template_for("Trane Technologies")["intro"].startswith("Trane")
    assert _template_for("Otsuka Pharmaceutical")["intro"].startswith("Otsuka")
    assert _template_for("Random Co")["intro"] == "Media monitoring digest"   # beone default


async def test_build_report_docx():
    store = get_artifact_store()
    async with get_sessionmaker()() as db, db.begin():
        project = Project(name="rep", brand_name="Trane")
        db.add(project)
        await db.flush()
        row = SessionRow(project_id=project.id, status="charts_ready",
                         config={"brand": "Trane"})
        db.add(row)
        await db.flush()
        sid = str(row.id)
        row.charts_data_file_key = keys.charts_data_file(sid)

    await store.put_json(keys.charts_data_file(sid), {"dashboards": {"media_monitoring": {
        "sections": [{"section": "Brand News", "articles": [
            {"id": "A0", "title": "Trane expands rebates", "publisher": "ACHR News",
             "date": "2026-07-15", "summary": "Details of the rebate expansion.",
             "url": "https://achrnews.com/x"}]}]}}})
    await store.put_json(keys.tagged_file(sid), {"articles": []})

    data = await build_report(session_id=sid, brand="Trane")
    assert data[:2] == b"PK"                                   # docx is a zip
    assert zipfile.is_zipfile(io.BytesIO(data))
    doc = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Trane" in text
    assert "Trane expands rebates" in text
    assert "Back to top" in text
