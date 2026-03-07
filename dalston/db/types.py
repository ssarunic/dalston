"""Dialect-portable SQLAlchemy custom types for Postgres + SQLite compatibility.

Each TypeDecorator maps to a native Postgres type on PostgreSQL and a
compatible primitive on SQLite, so the same model definitions work on both
without any dialect-specific imports in models.py.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator


class UUIDType(TypeDecorator[uuid.UUID]):
    """UUID: native PG_UUID (16-byte) on Postgres, CHAR(36) on SQLite."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect: Dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            # asyncpg accepts uuid.UUID objects natively
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return (
            str(value) if isinstance(value, uuid.UUID) else str(uuid.UUID(str(value)))
        )

    def process_result_value(self, value, dialect: Dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONType(TypeDecorator):
    """JSONB (indexable) on Postgres, TEXT with JSON serde on SQLite."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect: Dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect: Dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value


class InetType(TypeDecorator[str]):
    """INET (validated) on Postgres, VARCHAR(45) on SQLite."""

    impl = String(45)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(INET())
        return dialect.type_descriptor(String(45))

    def process_bind_param(self, value, dialect: Dialect):
        return value

    def process_result_value(self, value, dialect: Dialect):
        return value
