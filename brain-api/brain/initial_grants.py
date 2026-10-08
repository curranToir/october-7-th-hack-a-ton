"""The only default cross-user grants; client datasets keep their existing ACLs."""

from .registry import ENG, LEAD


async def apply_initial_read_grants(grant):
    for dataset in ("toir-firm", "toir-pipeline"):
        await grant(LEAD, ENG, dataset)
