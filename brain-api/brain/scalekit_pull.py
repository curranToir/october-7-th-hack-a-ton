from . import config
import asyncio
from datetime import datetime, timezone
import json
import os
import re
from scalekit import ScalekitClient
from respan import task
from .registry import CONNECTIONS, DATASETS, USERS, LEAD, SLACK, GITHUB, COMPANIES, user_name, document

_client = None

def provider_id(value):
    return str(int(value)) if isinstance(value, (int, float)) else str(value)

def actions():
    global _client
    if _client is None:
        _client = ScalekitClient(env_url=os.environ["SCALEKIT_ENVIRONMENT_URL"], client_id=os.environ["SCALEKIT_CLIENT_ID"], client_secret=os.environ["SCALEKIT_CLIENT_SECRET"])
    return _client.actions

async def active(source, email):
    try:
        result = await asyncio.to_thread(actions().get_or_create_connected_account, connection_name=CONNECTIONS[source], identifier=email)
    except Exception as error:
        if type(error).__name__ == "ScalekitNotFoundException":
            return False
        raise
    return result.connected_account.status == "ACTIVE"

async def authorization_link(source, email):
    try:
        result = await asyncio.to_thread(actions().get_authorization_link, connection_name=CONNECTIONS[source], identifier=email)
        return result.link
    except Exception as error:
        if type(error).__name__ == "ScalekitNotFoundException":
            raise RuntimeError(f"create Scalekit connection {CONNECTIONS[source]} first") from None
        raise

async def auth_links(email):
    user_name(email)
    links = {}
    for source, connection in CONNECTIONS.items():
        if not await active(source, email):
            try:
                links[connection] = await authorization_link(source, email)
            except RuntimeError as error:
                links[connection] = str(error)
    return links

async def actor(source, owner):
    for email in (owner, *(user for user in USERS if user != owner)):
        if await active(source, email):
            return email
    link = await authorization_link(source, owner)
    raise RuntimeError(f"authorize {owner} for {CONNECTIONS[source]}: {link}")

async def execute(source, email, name, **inputs):
    result = await asyncio.to_thread(actions().execute_tool, tool_name=name, tool_input=inputs, connection_name=CONNECTIONS[source], identifier=email)
    data = result.data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return data
    if isinstance(data, dict) and (data.get("ok") is False or data.get("error")):
        raise RuntimeError(f"{name}: {data.get('error')}")
    return data

async def pages(source, email, tool, key, pagination="cursor", **inputs):
    items = []
    seen = set()
    while True:
        result = await execute(source, email, tool, **inputs)
        if isinstance(result, list):
            batch = result
            token = inputs.get("page", 1) + 1 if pagination == "page" and len(batch) == inputs.get("per_page", 100) else None
        elif isinstance(result, dict):
            batch = result.get(key)
            if batch is None:
                # Scalekit's protobuf Struct wraps array-valued REST responses.
                arrays = [value for value in result.values() if isinstance(value, list)]
                if len(arrays) != 1:
                    raise RuntimeError(f"unexpected {tool} result envelope")
                batch = arrays[0]
            if pagination == "cursor":
                token = result.get("response_metadata", {}).get("next_cursor")
            elif pagination == "page_token":
                token = result.get("nextPageToken")
            elif pagination == "after":
                token = result.get("paging", {}).get("next", {}).get("after")
            else:
                token = inputs.get("page", 1) + 1 if len(batch) == inputs.get("per_page", 100) else None
        else:
            raise RuntimeError(f"unexpected {tool} response")
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected {tool} items")
        items.extend(batch)
        if not token:
            return items
        if str(token) in seen:
            raise RuntimeError(f"repeated {tool} pagination cursor")
        seen.add(str(token))
        inputs[pagination] = token

def recorded_path(source, container):
    # Readable names that stay inside the source directory and are valid on Windows checkouts too.
    name = re.sub(r'[\\/:*?"<>|]+\s*', "_", container)
    return config.ROOT / "data" / "recorded" / source / f"{name}.json"

def save(source, container, raw):
    path = recorded_path(source, container)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))

def iso(value):
    if value is None:
        return "unknown"
    try:
        stamp = float(value)
        if stamp > 100000000000:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError):
        return str(value).replace("+00:00", "Z")

def slack_line(channel, message, users):
    text = message.get("text", "")
    persona = re.match(r"^\*\*(.+? \(.+?\)):\*\*\s*", text)
    if persona:
        author, text = persona.group(1), text[persona.end():]
    else:
        author = users.get(message.get("user"), message.get("username", message.get("user", "unknown")))
    return f"[slack #{channel} · {author} · {iso(message.get('ts'))}] {text}"

def render(source, container, dataset, raw):
    if source == "slack":
        lines = []
        for message in sorted(raw["messages"], key=lambda item: float(item.get("ts", 0))):
            lines.append(slack_line(container, message, raw["users"]))
            lines.extend("  " + slack_line(container, reply, raw["users"]) for reply in message.get("replies", []))
        return [document(source, container, dataset, f"#{container}", raw.get("url", ""), "\n".join(lines))] if lines else []
    if source == "github":
        docs = []
        for issue in raw["items"]:
            n = provider_id(issue["number"])
            lines = [f"Title: {issue['title']}", f"State: {issue.get('state', '')}", "Labels: " + ", ".join(label['name'] for label in issue.get('labels', [])), "Assignees: " + ", ".join(user['login'] for user in issue.get('assignees', [])), issue.get("body") or ""]
            lines.extend(f"[github {container}#{n} · {comment.get('user', {}).get('login', 'unknown')} · {iso(comment.get('created_at'))}] {comment.get('body', '')}" for comment in issue.get("comments_data", []))
            docs.append(document(source, container, dataset, issue["title"], issue.get("html_url", ""), "\n".join(lines)))
        return docs
    if source == "hubspot":
        company = raw["company"]
        lines = ["Company properties: " + json.dumps(company.get("properties", {}), ensure_ascii=False)]
        for deal in raw.get("deals", []):
            props = deal.get("properties", {})
            lines.append("Deal: " + "; ".join(f"{key}={props.get(key, '')}" for key in ("dealname", "dealstage", "amount", "closedate", "hubspot_owner_id")))
        for contact in raw.get("contacts", []):
            props = contact.get("properties", {})
            lines.append("Contact: " + "; ".join(f"{key}={props.get(key, '')}" for key in ("firstname", "lastname", "jobtitle", "email")))
        for note in raw.get("notes", []):
            props = note.get("properties", {})
            lines.append(f"[hubspot {container} · note · {iso(props.get('hs_timestamp', note.get('createdAt')))}] {props.get('hs_note_body', '')}")
        return [document(source, container, dataset, container, company.get("url", ""), "\n".join(lines))]
    raise ValueError("unknown_source")

async def fetch_slack(container, dataset):
    email = await actor("slack", DATASETS[dataset].owner)
    channels = await pages("slack", email, "slack_list_channels", "channels", limit=100, types="public_channel,private_channel", exclude_archived=True)
    channel = next((item for item in channels if item.get("name") == container), None)
    if channel is None:
        return {"messages": [], "users": {}}
    members = await pages("slack", email, "slack_list_users", "members", limit=100)
    users = {item["id"]: item.get("real_name") or item.get("name", item["id"]) for item in members}
    messages = await pages("slack", email, "slack_fetch_conversation_history", "messages", channel=channel["id"], limit=100)
    for message in messages:
        if message.get("reply_count", 0):
            replies = await pages("slack", email, "slack_get_conversation_replies", "messages", channel=channel["id"], ts=message["ts"], limit=100)
            message["replies"] = [reply for reply in replies if reply.get("ts") != message["ts"]]
    return {"messages": messages, "users": users, "url": f"https://app.slack.com/archives/{channel['id']}"}

async def fetch_github(container, dataset):
    email = await actor("github", DATASETS[dataset].owner)
    args = {"owner": "Toir-FDE-Team", "repo": container, "state": "all", "per_page": 100, "page": 1}
    issues = await pages("github", email, "github_issues_list", "issues", "page", **args)
    prs = await pages("github", email, "github_pull_requests_list", "pull_requests", "page", **args)
    items = {item["number"]: item for item in issues + prs}
    for n, item in items.items():
        item["comments_data"] = await pages("github", email, "github_issue_comments_list", "comments", "page", owner="Toir-FDE-Team", repo=container, issue_number=n, per_page=100, page=1)
    return {"items": list(items.values())}

async def fetch_hubspot():
    email = await actor("hubspot", LEAD)
    companies = await pages("hubspot", email, "hubspot_companies_list", "results", "after", limit=100, properties="name,domain,industry,description")
    records = []
    for company in companies:
        name = company.get("properties", {}).get("name") or ""
        dataset = COMPANIES.get(name, "toir-pipeline" if name.startswith("Toir Prospect: ") else None)
        if not dataset:
            continue
        raw = {"company": company}
        for kind, props in {"deals": "dealname,dealstage,amount,closedate,hubspot_owner_id", "contacts": "firstname,lastname,jobtitle,email", "notes": "hs_note_body,hs_timestamp"}.items():
            associations = await pages("hubspot", email, "hubspot_record_associations_get", "results", "after", object_type="companies", object_id=provider_id(company["id"]), to_object_type=kind, limit=100)
            raw[kind] = []
            for association in associations:
                object_id = provider_id(association.get("toObjectId", association.get("id")))
                record = await execute("hubspot", email, "hubspot_record_with_history_get", object_type=kind, object_id=object_id, properties=props, properties_with_history=props)
                raw[kind].append(record)
        records.append((name, dataset, raw))
    return records

@task(name="brain.pull")
async def pull(as_user, sources=None, from_recorded=False):
    user_name(as_user)
    selected = list(CONNECTIONS) if sources is None else sources
    if any(source not in CONNECTIONS for source in selected):
        raise ValueError("unknown_source")
    from .memory import remember_docs
    counts, touched = {}, set()
    for source in selected:
        counts[source] = 0
        if source == "hubspot":
            if from_recorded:
                records = []
                for path in sorted((config.ROOT / "data/recorded/hubspot").glob("*.json")):
                    raw = json.loads(path.read_text())
                    name = raw["company"]["properties"].get("name") or ""
                    dataset = COMPANIES.get(name, "toir-pipeline" if name.startswith("Toir Prospect: ") else None)
                    if dataset:
                        records.append((name, dataset, raw))
            else:
                records = await fetch_hubspot()
        else:
            routing = SLACK if source == "slack" else GITHUB
            records = []
            for container, dataset in routing.items():
                if from_recorded:
                    path = recorded_path(source, container)
                    if not path.exists():
                        continue
                    raw = json.loads(path.read_text())
                else:
                    fetch = {"slack": fetch_slack, "github": fetch_github}[source]
                    raw = await fetch(container, dataset)
                records.append((container, dataset, raw))
        for container, dataset, raw in records:
            if not from_recorded:
                save(source, container, raw)
            docs = render(source, container, dataset, raw)
            if docs:
                counts[source] += await remember_docs(dataset, docs)
                touched.add(dataset)
    return {"items_by_source": counts, "datasets": sorted(touched)}
