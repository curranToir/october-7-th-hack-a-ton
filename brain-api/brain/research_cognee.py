"""Small adapter for Cognee 1.6.3's explicit RememberResult completion contract."""

from .registry import LEAD, user_name


async def resolve_pipeline_target(email, *, users, resolve):
    """Find the owner's canonical dataset, then check the real actor's grants by ID."""
    user_name(email)
    canonical = await resolve(["toir-pipeline"], "share", users[LEAD])
    if len(canonical) != 1 or canonical[0].name != "toir-pipeline":
        raise PermissionError("not_dataset_writer")
    dataset_id, actor = canonical[0].id, users[email]
    for permission in ("read", "write"):
        granted = await resolve([dataset_id], permission, actor)
        if len(granted) != 1 or granted[0].id != dataset_id:
            raise PermissionError("not_dataset_writer")
    return actor, dataset_id


async def remember_document(doc, *, remember, actor, dataset_id, graph_model, prompt):
    result = await remember(
        doc["text"],
        dataset_name="toir-pipeline",
        dataset_id=dataset_id,
        user=actor,
        node_set=doc["node_set"],
        graph_model=graph_model,
        custom_prompt=prompt,
        self_improvement=False,
        run_in_background=False,
    )
    # Neither a truthy result object nor successful transport proves completion.
    if (
        getattr(result, "status", None) != "completed"
        or getattr(result, "dataset_name", None) != "toir-pipeline"
        or str(getattr(result, "dataset_id", None)) != str(dataset_id)
    ):
        raise RuntimeError("research_document_not_completed")
    return {
        key: str(value)[:200]
        for key in ("pipeline_run_id", "dataset_id", "content_hash")
        if (value := getattr(result, key, None)) is not None
    }
