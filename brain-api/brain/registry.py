from dataclasses import dataclass
import re

LEAD = "jared@neptuneops.com"
ENG = "curran@toirinc.com"
USERS = (LEAD, ENG)
CONNECTIONS = {"slack": "slack", "github": "github-connect", "hubspot": "hubspot"}

@dataclass(frozen=True)
class Dataset:
    client: str
    layer: str
    owner: str

DATASETS = {
    "toir-firm": Dataset("toir", "firm", LEAD),
    "toir-pipeline": Dataset("toir", "commercial", LEAD),
    "acme-eng": Dataset("acme", "eng", LEAD),
    "acme-commercial": Dataset("acme", "commercial", LEAD),
    "initech-eng": Dataset("initech", "eng", LEAD),
    "initech-commercial": Dataset("initech", "commercial", LEAD),
    "globex-eng": Dataset("globex", "eng", ENG),
    "globex-commercial": Dataset("globex", "commercial", LEAD),
}
SLACK = {"toir-general": "toir-firm", "acme-eng": "acme-eng", "acme-deal": "acme-commercial", "initech-eng": "initech-eng", "initech-deal": "initech-commercial", "globex-eng": "globex-eng", "globex-deal": "globex-commercial"}
GITHUB = {"acme-agent-rollout": "acme-eng", "initech-evals": "initech-eng", "globex-clinical-rag": "globex-eng", "toir-playbooks": "toir-firm"}
COMPANIES = {"Acme Logistics": "acme-commercial", "Globex Health": "globex-commercial", "Initech Finance": "initech-commercial"}
ALIASES = {"acme": ("acme", "acme logistics"), "globex": ("globex", "globex health"), "initech": ("initech", "initech finance"), "toir": ("toir",)}

def user_name(email):
    if email not in USERS:
        raise ValueError("unknown_user")
    return email

def dataset_name(name):
    if name not in DATASETS:
        raise ValueError("unknown_dataset")
    return name

def withheld(question, readable):
    return [{"client": ds.client, "layer": ds.layer, "owner": ds.owner} for name, ds in DATASETS.items() if name not in readable and any(re.search(r"\b" + re.escape(alias) + r"\b", question, re.I) for alias in ALIASES[ds.client])]

def document(source, container, dataset, title, url, body):
    ds = DATASETS[dataset]
    clean = lambda value: str(value).replace("\n", " ").replace("]]", "]")
    text = f"[[source={source}; container={clean(container)}; client={ds.client}; layer={ds.layer}; title={clean(title)}; url={clean(url)}]]\n\n{body}"
    return {"text": text, "node_set": [f"source:{source}", f"client:{ds.client}", f"layer:{ds.layer}", f"container:{container}"]}

def sources_in(text):
    return sorted({f"source:{src}" for src in re.findall(r"(?:source=|\[)(slack|github|hubspot|research)(?=[;\s])", text)})

if __name__ == "__main__":
    assert withheld("Acme go-live", []) == [{"client": "acme", "layer": "eng", "owner": LEAD}, {"client": "acme", "layer": "commercial", "owner": LEAD}]
    assert document("slack", "acme-eng", "acme-eng", "Title", "", "body")["node_set"] == ["source:slack", "client:acme", "layer:eng", "container:acme-eng"]
    assert sources_in("[[source=slack; x]] [github repo]") == ["source:github", "source:slack"]
