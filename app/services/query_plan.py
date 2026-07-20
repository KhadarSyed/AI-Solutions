"""Build the Brand / Competitors / Industry query groups from resolved inputs."""


def build_query_plan(brand: str, competitors: list[str],
                     industry: str | None) -> list[dict]:
    plan: list[dict] = [
        {"name": "Brand News", "queries": [brand], "subject": brand,
         "subject_per_query": False},
    ]
    if competitors:
        plan.append({"name": "Competitors News", "queries": list(competitors),
                     "subject": None, "subject_per_query": True})
    if industry and industry.strip():
        plan.append({"name": "Industry News", "queries": [f"{industry} industry"],
                     "subject": "", "subject_per_query": False})
    return plan
