from app.db.models import Project


def test_project_model_has_competitors_column():
    assert "competitors" in Project.__table__.columns
    col = Project.__table__.columns["competitors"]
    assert col.nullable is False
