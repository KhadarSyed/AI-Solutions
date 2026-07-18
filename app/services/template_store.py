"""TemplateStore — when the user likes a dashboard, its layout becomes the
standing template for that project (and user); later dashboards reuse it."""

import contextlib

from app.artifacts.base import ArtifactNotFound
from app.artifacts.factory import get_artifact_store
from app.memory.mem0_service import MemoryType, remember


def _key(project_id: str, user_id: str | None = None) -> str:
    scope = f"user-{user_id}" if user_id else "project"
    return f"templates/{project_id}/{scope}/liked_dashboard.json"


def layout_of(schema: dict) -> dict:
    """The reusable part of a dashboard schema — look and structure, not data."""
    return {
        "theme": schema.get("theme", "dark"),
        "tab_order": [t["id"] for t in schema.get("tabs", [])],
        "chart_types": {c["id"]: c.get("option", {}).get("series", [{}])[0].get("type", c["engine"])
                        for c in schema.get("charts", [])},
        "banner": schema.get("banner", {}),
        "include_logos": bool(schema.get("logos")),
    }


async def save_liked(project_id: str, schema: dict, user_id: str | None = None) -> dict:
    template = layout_of(schema)
    await get_artifact_store().put_json(_key(project_id, user_id), template)
    with contextlib.suppress(Exception):
        await remember(
            agent="dashboard", memory_type=MemoryType.PROFILE, project_id=project_id,
            user_id=user_id,
            content=(f"Liked dashboard template: theme={template['theme']}, "
                     f"tabs={','.join(template['tab_order'])} — reuse this layout."),
        )
    return template


async def load_liked(project_id: str, user_id: str | None = None) -> dict | None:
    store = get_artifact_store()
    for key in (_key(project_id, user_id), _key(project_id, None)):
        try:
            return await store.get_json(key)
        except ArtifactNotFound:
            continue
    return None
