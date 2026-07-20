from app.agents.email_agent import subject_allowed


def test_monitor_brand_is_canonical_subject():
    # the canonical trigger subjects
    assert subject_allowed("Monitor BeOne", ["BeOne", "Trane", "Otsuka"])
    assert subject_allowed("Monitor Trane", ["BeOne", "Trane", "Otsuka"])
    assert subject_allowed("Monitor Otsuka", ["BeOne", "Trane", "Otsuka"])


def test_start_subject_allowed():
    assert subject_allowed("Start BeOne monitoring", ["BeOne", "Trane"])
    assert subject_allowed("Re: [BEONE-20260720-001] approve", ["BeOne"])


def test_unrelated_subject_ignored():
    assert not subject_allowed("Your invoice is due", ["BeOne", "Trane"])
    assert not subject_allowed("", ["BeOne"])
