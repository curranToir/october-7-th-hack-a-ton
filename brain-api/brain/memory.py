from . import config as config
import asyncio
import secrets
from pathlib import Path
import cognee
from respan import task, workflow, respan_span_attributes
from opentelemetry import trace
from cognee.modules.users.methods import create_user, get_user_by_email
from cognee.modules.data.methods import create_authorized_dataset, get_authorized_existing_datasets, has_dataset_data
from cognee.modules.users.permissions.methods import authorized_give_permission_on_datasets, authorized_revoke_permission_on_datasets
from cognee.modules.search.types import SearchType
from .registry import DATASETS, USERS, LEAD, user_name, dataset_name, withheld, sources_in
from .graph_model import CompanyGraph, EXTRACTION_PROMPT
from .initial_grants import apply_initial_read_grants
from .research_cognee import remember_document

# ponytail: one process-local lock; move to a dedicated writer only if throughput requires it.
writer_lock = asyncio.Lock()
_users = {}

async def ensure_users():
    if _users:
        return _users
    await cognee.run_migrations()
    for email in USERS:
        user = await get_user_by_email(email)
        if user is None:
            user = await create_user(email, secrets.token_urlsafe(32), is_verified=True)
        _users[email] = user
    return _users

async def initialize():
    await ensure_users()
    for name, spec in DATASETS.items():
        owner = _users[spec.owner]
        existing = await get_authorized_existing_datasets([name], "share", owner)
        if not existing:
            await create_authorized_dataset(name, owner)
    await apply_initial_grants()

async def readable(email):
    user_name(email)
    await ensure_users()
    return [ds for ds in await get_authorized_existing_datasets(None, "read", _users[email]) if ds.name in DATASETS]

async def access(email):
    datasets = await readable(email)
    return {"readable": sorted(ds.name for ds in datasets), "owned": sorted(ds.name for ds in datasets if DATASETS[ds.name].owner == email)}

async def apply_initial_grants():
    await apply_initial_read_grants(grant)

async def grant(owner, grantee, dataset):
    return await permission(owner, grantee, dataset, False)

async def revoke(owner, grantee, dataset):
    return await permission(owner, grantee, dataset, True)

async def permission(owner, grantee, dataset, revoke):
    user_name(owner); user_name(grantee); dataset_name(dataset)
    if DATASETS[dataset].owner != owner or owner == grantee:
        raise PermissionError("not_dataset_owner")
    await ensure_users()
    ds = await get_authorized_existing_datasets([dataset], "share", _users[owner])
    if not ds:
        ds = [await create_authorized_dataset(dataset, _users[owner])]
    method = authorized_revoke_permission_on_datasets if revoke else authorized_give_permission_on_datasets
    await method(_users[grantee].id, [ds[0].id], "read", _users[owner].id)
    return {"readable": (await access(grantee))["readable"]}

@task(name="brain.remember")
async def remember_docs(dataset, docs):
    dataset_name(dataset)
    await ensure_users()
    owner = _users[DATASETS[dataset].owner]
    for doc in docs:
        await cognee.remember(doc["text"], dataset_name=dataset, user=owner, node_set=doc["node_set"], graph_model=CompanyGraph, custom_prompt=EXTRACTION_PROMPT, self_improvement=False, run_in_background=False)
    if docs:
        await cognee.improve(dataset=dataset, user=owner, session_ids=[])
    await apply_initial_grants()
    return len(docs)

@task(name="brain.recall.graph")
async def graph_recall(question, datasets, user, top_k, only_context, session_id=None):
    try:
        return await cognee.recall(question, query_type=SearchType.GRAPH_COMPLETION, dataset_ids=[ds.id for ds in datasets], user=user, top_k=top_k, auto_route=False, scope=["graph"], only_context=only_context, session_id=session_id)
    except Exception as error:
        if type(error).__name__ in {"DatasetNotFoundError", "NoDataError", "NoDataFoundError"}:
            return []
        raise

@workflow(name="brain.recall")
async def recall(user, question, mode="answer", session_id=None, top_k=10):
    user_name(user)
    datasets = await readable(user)
    names = sorted(ds.name for ds in datasets)
    hidden = withheld(question, names)
    attrs = {"as_user": user, "mode": mode, "datasets_searched": names, "withheld_count": len(hidden)}
    respan_span_attributes({"metadata": attrs})
    for key, value in attrs.items():
        trace.get_current_span().set_attribute(key, value)
    response = {"answer": None, "context": [], "sources": [], "datasets_searched": names, "withheld": hidden}
    populated = [ds for ds in datasets if await has_dataset_data(ds.id)]
    if not populated:
        return response
    for ds in populated:
        hits = await graph_recall(question, [ds], _users[user], top_k, True)
        for hit in hits:
            if getattr(hit, "source", "graph") != "graph":
                continue
            text = getattr(hit, "text", "")
            if text:
                response["context"].append({"text": text, "dataset": ds.name, "sources": sources_in(text)})
    response["sources"] = sorted({source for item in response["context"] for source in item["sources"]})
    if mode == "answer":
        hits = await graph_recall(question, populated, _users[user], top_k, False, session_id)
        answers = [getattr(hit, "text", "") for hit in hits if getattr(hit, "source", "graph") == "graph"]
        response["answer"] = "\n\n".join(text for text in answers if text) or None
    return response

async def forget(email, dataset=None):
    user_name(email)
    if dataset is not None:
        dataset_name(dataset)
        if DATASETS[dataset].owner != email:
            raise PermissionError("not_dataset_owner")
    datasets = [ds for ds in await readable(email) if DATASETS[ds.name].owner == email and (dataset is None or ds.name == dataset)]
    for ds in datasets:
        await cognee.forget(dataset_id=ds.id, user=_users[email])
    return {"forgotten": sorted(ds.name for ds in datasets)}

async def remember_research_document(doc):
    """Caller holds writer_lock and has durably marked this specific document started."""
    await ensure_users()
    return await remember_document(
        doc, remember=cognee.remember, owner=_users[LEAD],
        graph_model=CompanyGraph, prompt=EXTRACTION_PROMPT,
    )

async def graph(dataset):
    dataset_name(dataset)
    await ensure_users()
    owner = _users[DATASETS[dataset].owner]
    datasets = await get_authorized_existing_datasets([dataset], "read", owner)
    if not datasets or not await has_dataset_data(datasets[0].id):
        return "<!doctype html><html><body><h1>Empty graph</h1></body></html>"
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "graph.html"
        await cognee.visualize_graph(destination_file_path=str(path), user=owner, dataset=dataset, include_session_events=False)
        return path.read_text()
