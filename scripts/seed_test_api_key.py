#!/usr/bin/env python3
"""Seed the database with a known test API key for Playwright E2E tests.

This script inserts the hardcoded test API key used by Playwright tests.
Run this before executing web E2E tests:

    python scripts/seed_test_api_key.py

The API key is: dk_PE2-k0faXI3JBhW-tYWqPPzbJxpqlWHsXG_SMNZU8bo
"""

import asyncio
import hashlib
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

# Ensure dalston package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from dalston.db.models import APIKeyModel
from dalston.db.session import DEFAULT_TENANT_ID, async_session, init_db
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, Scope

# The hardcoded test API key used in Playwright tests
TEST_API_KEY = "dk_PE2-k0faXI3JBhW-tYWqPPzbJxpqlWHsXG_SMNZU8bo"
TEST_KEY_HASH = hashlib.sha256(TEST_API_KEY.encode()).hexdigest()
TEST_KEY_PREFIX = TEST_API_KEY[:10]


async def seed_test_api_key() -> bool:
    """Insert the test API key if it doesn't exist.

    Returns:
        True if key was inserted, False if it already existed.
    """
    await init_db()

    async with async_session() as db:
        # Check if key already exists
        stmt = select(APIKeyModel).where(APIKeyModel.key_hash == TEST_KEY_HASH)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Test API key already exists (prefix: {TEST_KEY_PREFIX}...)")
            return False

        # Create the test API key
        model = APIKeyModel(
            id=uuid4(),
            key_hash=TEST_KEY_HASH,
            prefix=TEST_KEY_PREFIX,
            name="Playwright Test Admin Key",
            tenant_id=DEFAULT_TENANT_ID,
            scopes=Scope.ADMIN.value,
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        db.add(model)
        await db.commit()

        print(f"Created test API key: {TEST_KEY_PREFIX}...")
        print(f"  Prefix: {TEST_KEY_PREFIX}")
        print(f"  Tenant: {DEFAULT_TENANT_ID}")
        print(f"  Scopes: {Scope.ADMIN.value}")
        return True


if __name__ == "__main__":
    result = asyncio.run(seed_test_api_key())
    sys.exit(0 if result else 0)  # Always exit 0 (success if key exists or was created)
