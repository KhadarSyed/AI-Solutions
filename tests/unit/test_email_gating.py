from app.agents.email_agent import subject_allowed


def test_start_subject_allowed():
    assert subject_allowed("Start BeOne monitoring", ["BeOne", "Trane"])
    assert subject_allowed("Re: [BEONE-20260720-001] approve", ["BeOne"])


def test_unrelated_subject_ignored():
    assert not subject_allowed("Your invoice is due", ["BeOne", "Trane"])
    assert not subject_allowed("", ["BeOne"])
