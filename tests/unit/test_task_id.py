from app.orchestration.task_id import extract_task_id, slug_for, task_tag


def test_task_tag_format():
    assert task_tag("BEONE-20260721-001") == "[BEONE-20260721-001]"


def test_extract_task_id_from_subject():
    assert extract_task_id("Re: [BEONE-20260721-001] approve") == "BEONE-20260721-001"
    assert extract_task_id("no tag here") is None


def test_slug_for_brand():
    assert slug_for("BeOne") == "BEONE"
    assert slug_for("Johnson & Johnson") == "JOHNSONJ"   # non-alnum stripped, capped at 8
    assert slug_for("") == "TASK"
