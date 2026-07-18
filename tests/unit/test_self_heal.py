from app.orchestration.self_heal import error_signature


def test_signature_stable_across_addresses_and_ids():
    a = error_signature("TimeoutError: waiting for selector at 0x7f3a9b2c failed after 30000ms")
    b = error_signature("TimeoutError: waiting for selector at 0x1c2d3e4f failed after 45000ms")
    assert a == b


def test_signature_uses_last_line():
    text = "Traceback (most recent call last):\n  File x\nValueError: bad thing"
    assert error_signature(text).startswith("ValueError: bad thing")
