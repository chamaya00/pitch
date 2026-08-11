"""The credential half of the owner store, behind its own protocol.

One table, one implementation, two protocols. ``OwnerRepository`` is about
owners; ``CredentialRepository`` is about the ways in to one. Splitting the
*interfaces* rather than the storage means the resolver depends on exactly the
three operations it needs and cannot reach an owner's recordings, while the
identity routes depend on the credential operations and nothing else.
"""

import uuid

from app.db.pool import Database
from app.services.owners.credentials import Credential
from app.services.owners.repository import PostgresOwnerRepository


class PostgresCredentialRepository:
    """Credentials in PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self._owners = PostgresOwnerRepository(database)

    async def create(self, credential: Credential, key: str) -> Credential:
        return await self._owners.create_credential(credential, key)

    async def owner_for_key(self, key: str) -> uuid.UUID | None:
        return await self._owners.owner_for_key(key)

    async def credential_for_key(self, key: str) -> uuid.UUID | None:
        return await self._owners.credential_for_key(key)

    async def list_for_owner(self, owner_id: uuid.UUID) -> list[Credential]:
        return await self._owners.list_credentials(owner_id)

    async def revoke(self, credential_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        return await self._owners.revoke_credential(credential_id, owner_id)
