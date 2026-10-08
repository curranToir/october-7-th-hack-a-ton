"""The only default cross-user grants; client datasets keep their existing ACLs."""

from .registry import DATASETS, INITIAL_GRANTS


async def apply_initial_grants(grant):
    for dataset, grantee, permission in INITIAL_GRANTS:
        await grant(DATASETS[dataset].owner, grantee, dataset, permission)
