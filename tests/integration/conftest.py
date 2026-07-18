import pytest

from app.db.base import get_sessionmaker
from app.db.models import Project
from app.db.models import Session as SessionRow


@pytest.fixture
async def blank_session() -> dict:
    """A real project + session with empty query groups — the whole pipeline can
    run offline against it (0 articles end-to-end)."""
    async with get_sessionmaker()() as db, db.begin():
        project = Project(name="graph-test", brand_name="Trane")
        db.add(project)
        await db.flush()
        row = SessionRow(project_id=project.id,
                         config={"brand": "Trane", "query_groups": []})
        db.add(row)
        await db.flush()
        return {"project_id": str(project.id), "session_id": str(row.id)}
