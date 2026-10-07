from typing import Annotated
from cognee.low_level import DataPoint, FromIdentity

class Client(DataPoint):
    name: str
    metadata: dict = {"index_fields": ["name"], "identity_fields": ["name"]}

class Person(DataPoint):
    name: str
    role: str | None = None
    works_on: Annotated[list[Client], FromIdentity()] = []
    metadata: dict = {"index_fields": ["name"], "identity_fields": ["name"]}

class Deal(DataPoint):
    name: str
    stage: str | None = None
    amount: str | None = None
    close_date: str | None = None
    owns: Annotated[Client, FromIdentity()] | None = None
    requested_by: Annotated[Person, FromIdentity()] | None = None
    metadata: dict = {"index_fields": ["name"], "identity_fields": ["name"]}

class SOW(DataPoint):
    name: str
    text: str = ""
    owns: Annotated[Client, FromIdentity()] | None = None
    metadata: dict = {"index_fields": ["name", "text"], "identity_fields": ["name"]}

class EngineeringRequest(DataPoint):
    name: str
    text: str = ""
    status: str | None = None
    requested_by: Annotated[Person, FromIdentity()] | None = None
    metadata: dict = {"index_fields": ["name", "text"], "identity_fields": ["name"]}

class Commitment(DataPoint):
    name: str
    text: str
    due_date: str | None = None
    committed_in: Annotated[SOW, FromIdentity()] | None = None
    blocked_by: Annotated[list[EngineeringRequest], FromIdentity()] = []
    requested_by: Annotated[Person, FromIdentity()] | None = None
    metadata: dict = {"index_fields": ["name", "text"], "identity_fields": ["name"]}

class Deployment(DataPoint):
    name: str
    text: str
    deployed_for: Annotated[Client, FromIdentity()] | None = None
    blocked_by: Annotated[list[EngineeringRequest], FromIdentity()] = []
    metadata: dict = {"index_fields": ["name", "text"], "identity_fields": ["name"]}

class Decision(DataPoint):
    name: str
    text: str
    decided_in: Annotated[SOW, FromIdentity()] | None = None
    requested_by: Annotated[Person, FromIdentity()] | None = None
    metadata: dict = {"index_fields": ["name", "text"], "identity_fields": ["name"]}

class CompanyGraph(DataPoint):
    clients: list[Client] = []
    people: list[Person] = []
    deals: list[Deal] = []
    sows: list[SOW] = []
    commitments: list[Commitment] = []
    engineering_requests: list[EngineeringRequest] = []
    deployments: list[Deployment] = []
    decisions: list[Decision] = []

EXTRACTION_PROMPT = """Extract only facts explicitly stated in the source text into clients, people, deals, sows, commitments, engineering_requests, deployments, and decisions. Preserve exact names and source citations (source, container, URL, timestamp) in text fields. Include every referenced entity in its matching top-level list. Do not infer dates, commercial terms, owners or relationships not present in the text."""
