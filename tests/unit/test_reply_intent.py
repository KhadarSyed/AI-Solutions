"""Gate reply classification — scope-change vs approve vs add-a-data-point (the
deterministic path used before any LLM call)."""
from app.agents.email_agent import _ADD_DP_RE, _APPROVE_RE, _CHANGES_RE


def _decide(text: str) -> str | None:
    # mirrors _classify's deterministic gate path
    if _CHANGES_RE.search(text):
        return "changes"
    if _APPROVE_RE.search(text) or _ADD_DP_RE.search(text):
        return "approved"
    return None


def test_plain_approvals():
    for t in ["approve", "Yes, go ahead", "looks good", "sign off", "ok proceed",
              "Approved 👍", "good to go"]:
        assert _decide(t) == "approved", t


def test_scope_changes_loop():
    for t in ["change the date range", "add competitor Carrier", "remove Amway",
              "use the last 14 days instead", "redo with different sections"]:
        assert _decide(t) == "changes", t


def test_add_data_point_proceeds():
    # adding a data point is NOT a scope change — proceed, and capture it downstream
    for t in ["approve, also add pricing mentions", "add layoffs signal",
              "include ESG coverage", "also track product recalls"]:
        assert _decide(t) == "approved", t


def test_add_competitor_is_change_not_add():
    assert _decide("add competitor Daikin") == "changes"
