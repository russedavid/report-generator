"""Optional passage selection; ranking and source eligibility remain separate."""


def select_context(index, query, *, model, revision=None, actor=None,
                   policy="top_two", dependencies=None, max_chars=9000, limit=2):
    if policy not in {"top_two", "primary_with_dependencies"}:
        raise ValueError("Unknown context-selection policy")
    if not isinstance(limit, int) or not 1 <= limit <= 4 or max_chars < 0:
        raise ValueError("Invalid context-selection budget")
    dependencies = dependencies or {}
    if not isinstance(dependencies, dict) or any(
        not isinstance(key, str) or not isinstance(values, list)
        or any(not isinstance(value, str) for value in values)
        for key, values in dependencies.items()
    ):
        raise ValueError("Dependencies must map passage IDs to lists of passage IDs")
    ranked = index.search(query, model=model, revision=revision, actor=actor, limit=limit)
    trace = {"policy": policy, "ranked_ids": [d["id"] for d in ranked],
             "selected_ids": [], "withheld_reason": None}
    if policy == "top_two":
        selected = []
        remaining = max_chars
        for doc in ranked:
            if len(doc["text"]) <= remaining:
                selected.append(doc)
                remaining -= len(doc["text"])
    else:
        selected, seen = [], set()

        def include(doc_id):
            if doc_id in seen:
                return
            seen.add(doc_id)
            doc = index.lookup(doc_id, model=model, revision=revision, actor=actor)
            if doc is None:
                raise ValueError("required_passage_unavailable")
            selected.append(doc)
            if len(selected) > limit:
                raise ValueError("dependency_count_budget")
            if sum(len(d["text"]) for d in selected) > max_chars:
                raise ValueError("dependency_character_budget")
            for required in dependencies.get(doc_id, []):
                include(required)

        if ranked:
            try:
                include(ranked[0]["id"])
                selected[0]["retrieval_score"] = ranked[0]["retrieval_score"]
            except ValueError as error:
                selected = []
                trace["withheld_reason"] = str(error)
    trace["selected_ids"] = [d["id"] for d in selected]
    trace["selected_characters"] = sum(len(d["text"]) for d in selected)
    return selected, trace
