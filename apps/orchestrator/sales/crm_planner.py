"""Turn evidence into exact, reviewable changes without mutating HubSpot."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime

from apps.orchestrator.sales.crm import (
    AmbiguousMatch,
    CRMError,
    ScalekitCRM,
    canonical_domain,
    same_value,
)
from apps.orchestrator.sales.models import Contact, ContactReport, CRMOperation, Proposal


def linkedin_key(url: str) -> str:
    return "linkedin:" + hashlib.sha256(url.encode()).hexdigest()


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def cited_text(citations, sources) -> str:
    fragments = []
    for citation in citations:
        source = sources.get(citation.source_id)
        if source is None or normalize(citation.quote) not in normalize(source.text):
            raise CRMError("Research citations could not be verified.", code="evidence")
        fragments.append(citation.quote)
    return " ".join(fragments)


def contact_properties(contact: Contact, company_name: str, sources) -> dict:
    """Only copy a contact channel when its own citations contain that channel."""
    evidence = cited_text(contact.citations, sources)
    if normalize(contact.name) not in normalize(evidence):
        raise CRMError("Contact name is not supported by its evidence.", code="evidence")
    first, _, last = contact.name.strip().partition(" ")
    result = {"firstname": first, "jobtitle": contact.title, "company": company_name}
    if last:
        result["lastname"] = last
    if contact.email:
        email_text = cited_text(contact.email_citations, sources)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact.email) or (
            contact.email.casefold() not in email_text.casefold()
        ):
            raise CRMError("Contact email is not supported by its evidence.", code="evidence")
        result["email"] = contact.email.lower()
    if contact.phone:
        phone_text = cited_text(contact.phone_citations, sources)
        digits = re.sub(r"\D", "", contact.phone)
        if len(digits) < 7 or digits not in re.sub(r"\D", "", phone_text):
            raise CRMError("Contact phone is not supported by its evidence.", code="evidence")
        result["phone"] = contact.phone
    if contact.linkedin_url:
        text = cited_text(contact.linkedin_citations, sources)
        source_urls = [
            str(sources[c.source_id].url).rstrip("/") for c in contact.linkedin_citations
        ]
        if contact.linkedin_url not in text and contact.linkedin_url not in source_urls:
            raise CRMError("LinkedIn profile is not supported by its evidence.", code="evidence")
    return result


def changed_fields(desired: dict, current: dict) -> tuple[dict, dict]:
    changes = {
        key: value for key, value in desired.items() if not same_value(current.get(key), value)
    }
    return changes, {key: current.get(key) for key in changes}


def association(source: CRMOperation, target: CRMOperation, *, contact_id=None) -> CRMOperation:
    return CRMOperation(
        kind="association",
        action="associate",
        contact_id=contact_id,
        properties={
            "from_object_type": "notes" if source.kind == "note" else "contacts",
            "from_object_id": f"op:{source.id}",
            "to_object_type": "companies" if target.kind == "company" else "contacts",
            "to_object_id": f"op:{target.id}",
        },
        depends_on=[source.id, target.id],
    )


class CRMPlanner:
    def __init__(self, crm: ScalekitCRM, store=None):
        self.crm, self.store = crm, store

    async def _contact_match(self, contact: Contact, properties: dict, company_id: str | None):
        if self.store is not None and contact.linkedin_url:
            async with self.store.transaction() as tx:
                mapping = await tx.get("identity", linkedin_key(contact.linkedin_url))
            if mapping and mapping.get("crm_record_id"):
                record = await self.crm.get_record("contact", mapping["crm_record_id"])
                # Only a mapping made after a successful, approved write is accepted.
                full_name = " ".join(
                    str(record["properties"].get(k) or "") for k in ("firstname", "lastname")
                )
                if normalize(full_name) != normalize(contact.name):
                    raise AmbiguousMatch(
                        "A known LinkedIn mapping now identifies a different name."
                    )
                return record
        return await self.crm.find_contact(contact.name, properties.get("email"), company_id)

    async def plan(
        self,
        report: ContactReport,
        *,
        session_id: str,
        job_id: str,
        requested_by: str,
    ) -> Proposal:
        if report.company is None:
            raise CRMError(
                "Resolve a single company before preparing CRM changes.", code="identity"
            )
        company = report.company
        sources = {source.id: source for source in report.sources}
        cited_text(company.citations, sources)
        desired = {"name": company.name, "domain": company.domain}
        for key, value in (
            ("description", company.description),
            ("country", company.country),
            ("numberofemployees", company.employee_count),
        ):
            if value not in (None, ""):
                desired[key] = value
        existing = await self.crm.find_company(company.domain)
        changes, before = changed_fields(desired, existing["properties"] if existing else {})
        company_op = CRMOperation(
            kind="company",
            action="update" if existing else "create",
            record_id=str(existing["id"]) if existing else None,
            properties=changes if existing else desired,
            before=before if existing else {},
        )
        proposal = Proposal(
            session_id=session_id,
            job_id=job_id,
            requested_by=requested_by,
            company=company,
            sources=report.sources,
            gaps=list(report.gaps),
            operations=[company_op],
        )
        stamp = int(datetime.now(UTC).timestamp() * 1000)
        note = self._note(
            proposal,
            company.citations,
            sources,
            f"Company: {company.name}\nDomain: {company.domain}\n{company.description}\n\n"
            f"Suggested sales angle (inference): {company.sales_angle}",
            stamp,
        )
        note.depends_on = [company_op.id]
        proposal.operations.extend([note, association(note, company_op)])
        seen_people: set[str] = set()
        for contact in report.contacts:
            if canonical_domain(contact.company_domain) != company.domain:
                proposal.gaps.append(f"Excluded {contact.name}: company identity does not match.")
                continue
            identity = contact.linkedin_url or (contact.email or normalize(contact.name))
            if identity in seen_people:
                proposal.gaps.append(f"Excluded duplicate contact: {contact.name}.")
                continue
            seen_people.add(identity)
            try:
                props = contact_properties(contact, company.name, sources)
                match = await self._contact_match(contact, props, company_op.record_id)
            except AmbiguousMatch:
                proposal.gaps.append(
                    f"Excluded {contact.name}: resolve the ambiguous HubSpot match first."
                )
                continue
            except CRMError as error:
                if error.code != "evidence":
                    raise
                proposal.gaps.append(f"Excluded {contact.name}: {error}")
                continue
            changes, before = changed_fields(props, match["properties"] if match else {})
            contact_op = CRMOperation(
                kind="contact",
                action="update" if match else "create",
                contact_id=contact.id,
                record_id=str(match["id"]) if match else None,
                properties=changes if match else props,
                before=before if match else {},
                depends_on=[company_op.id],
            )
            proposal.contacts.append(contact)
            proposal.operations.extend(
                [
                    contact_op,
                    association(contact_op, company_op, contact_id=contact.id),
                ]
            )
            contact_note = self._note(
                proposal,
                contact.citations
                + contact.linkedin_citations
                + contact.email_citations
                + contact.phone_citations,
                sources,
                f"Contact: {contact.name}\nCurrent role: {contact.title}\nCompany: {company.name}\n"
                f"LinkedIn: {contact.linkedin_url or 'Not found'}\n"
                f"Public business email: {props.get('email', 'Not found')}\n"
                f"Public business phone: {props.get('phone', 'Not found')}\n\n"
                f"Suggested buying relevance (inference): {contact.buying_relevance}",
                stamp,
                contact_id=contact.id,
            )
            contact_note.depends_on = [company_op.id, contact_op.id]
            proposal.operations.extend(
                [
                    contact_note,
                    association(contact_note, company_op, contact_id=contact.id),
                    association(contact_note, contact_op, contact_id=contact.id),
                ]
            )
        return proposal

    @staticmethod
    def _note(proposal, citations, sources, text, stamp, *, contact_id=None):
        cited_text(citations, sources)
        operation = CRMOperation(kind="note", action="create", contact_id=contact_id)
        links = []
        for citation in citations:
            source = sources[citation.source_id]
            # Everything returned by research is text, never executable HTML or tool parameters.
            link = (
                f'<a href="{html.escape(str(source.url), quote=True)}">'
                f"{html.escape(source.title)}</a>: {html.escape(citation.quote)}"
            )
            if link not in links:
                links.append(link)
        operation.properties = {
            "hs_note_body": "<p>"
            + html.escape(text).replace("\n", "<br>")
            + "</p>"
            + "<p>Evidence:</p><ul><li>"
            + "</li><li>".join(links)
            + "</li></ul>"
            + f"<p>Toir research {proposal.id}; operation {operation.id}</p>",
            "hs_timestamp": stamp,
        }
        return operation
