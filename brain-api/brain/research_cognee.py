"""Small adapter for Cognee 1.6.3's explicit RememberResult completion contract."""


async def remember_document(doc, *, remember, owner, graph_model, prompt):
    result = await remember(
        doc["text"],
        dataset_name="toir-pipeline",
        user=owner,
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
    ):
        raise RuntimeError("research_document_not_completed")
    return {
        key: str(value)[:200]
        for key in ("pipeline_run_id", "dataset_id", "content_hash")
        if (value := getattr(result, key, None)) is not None
    }
