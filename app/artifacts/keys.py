"""S3-style artifact keys — the only place keys are built.

Sessions store these strings as pointers; swapping the storage backend never
changes a key.
"""

from uuid import UUID


def source_file(session_id: UUID | str) -> str:
    return f"sessions/{session_id}/source_file.json"


def tagged_file(session_id: UUID | str) -> str:
    return f"sessions/{session_id}/tagged_file.json"


def charts_data_file(session_id: UUID | str) -> str:
    return f"sessions/{session_id}/charts_data_file.json"


def upload(session_id: UUID | str, filename: str) -> str:
    return f"uploads/{session_id}/{filename}"


def report(session_id: UUID | str, filename: str) -> str:
    return f"reports/{session_id}/{filename}"


def gate_csv(session_id: UUID | str, gate: int) -> str:
    return f"sessions/{session_id}/gate{gate}_review.csv"


REACH_CSV = "reference/reach.csv"
