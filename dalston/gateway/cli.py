"""Dalston Gateway CLI commands.

Usage:
    python -m dalston.gateway.cli create-admin-key --name "Admin"
"""

import asyncio
import sys
from typing import Annotated

import typer

from dalston.db.session import DEFAULT_TENANT_ID
from dalston.gateway.services.auth import AuthService, Scope

app = typer.Typer(help="Dalston Gateway CLI.")


async def _create_admin_key(name: str) -> tuple[str, str]:
    """Create an admin API key.

    Args:
        name: Human-readable name for the key

    Returns:
        Tuple of (raw_key, key_id)
    """
    from dalston.common.redis import get_redis

    redis = await get_redis()
    auth_service = AuthService(redis)

    raw_key, api_key = await auth_service.create_api_key(
        name=name,
        tenant_id=DEFAULT_TENANT_ID,
        scopes=[Scope.ADMIN],
        rate_limit=None,  # Unlimited
    )

    return raw_key, str(api_key.id)


@app.command("create-admin-key")
def create_admin_key(
    name: Annotated[
        str,
        typer.Option(
            "--name",
            "-n",
            help="Human-readable name for the API key",
        ),
    ] = "Admin",
) -> None:
    """Create an admin API key for the default tenant.

    The key is displayed once and cannot be retrieved later.
    Store it securely!

    Example:
        python -m dalston.gateway.cli create-admin-key --name "My Admin Key"
    """
    typer.echo(f"Creating admin API key '{name}'...")

    try:
        raw_key, key_id = asyncio.run(_create_admin_key(name))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    typer.echo()
    typer.echo("=" * 60)
    typer.echo("Admin API key created successfully!")
    typer.echo("=" * 60)
    typer.echo()
    typer.echo(f"Key ID:  {key_id}")
    typer.echo(f"API Key: {raw_key}")
    typer.echo()
    typer.echo("IMPORTANT: Store this key securely!")
    typer.echo("It cannot be retrieved later.")
    typer.echo()
    typer.echo("=" * 60)
    typer.echo("Usage Examples")
    typer.echo("=" * 60)
    typer.echo()
    typer.echo("# Set as environment variable")
    typer.echo(f'export DALSTON_API_KEY="{raw_key}"')
    typer.echo()
    typer.echo("# Use with curl")
    typer.echo("curl -X POST http://localhost:8000/v1/audio/transcriptions \\")
    typer.echo(f'  -H "Authorization: Bearer {raw_key}" \\')
    typer.echo('  -F "file=@audio.mp3"')
    typer.echo()
    typer.echo("# Use with dalston-cli")
    typer.echo(f'dalston --api-key "{raw_key}" transcribe audio.mp3')
    typer.echo()


@app.command("list-keys")
def list_keys() -> None:
    """List all API keys for the default tenant."""

    async def _list_keys():
        from dalston.common.redis import get_redis

        redis = await get_redis()
        auth_service = AuthService(redis)
        return await auth_service.list_api_keys(DEFAULT_TENANT_ID)

    try:
        keys = asyncio.run(_list_keys())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not keys:
        typer.echo("No API keys found.")
        return

    typer.echo(f"Found {len(keys)} API key(s):")
    typer.echo()

    for key in keys:
        typer.echo(f"  ID:      {key.id}")
        typer.echo(f"  Prefix:  {key.prefix}...")
        typer.echo(f"  Name:    {key.name}")
        typer.echo(f"  Scopes:  {', '.join(s.value for s in key.scopes)}")
        typer.echo(f"  Created: {key.created_at.isoformat()}")
        if key.last_used_at:
            typer.echo(f"  Used:    {key.last_used_at.isoformat()}")
        typer.echo()


@app.command("revoke-key")
def revoke_key(
    key_id: Annotated[
        str,
        typer.Argument(help="Key ID to revoke"),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation",
        ),
    ] = False,
) -> None:
    """Revoke an API key by ID."""
    from uuid import UUID

    try:
        key_uuid = UUID(key_id)
    except ValueError:
        typer.echo(f"Error: Invalid key ID format: {key_id}", err=True)
        sys.exit(1)

    if not yes:
        typer.confirm(f"Revoke API key {key_id}?", abort=True)

    async def _revoke_key():
        from dalston.common.redis import get_redis

        redis = await get_redis()
        auth_service = AuthService(redis)
        return await auth_service.revoke_api_key(key_uuid)

    try:
        success = asyncio.run(_revoke_key())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if success:
        typer.echo(f"API key {key_id} revoked.")
    else:
        typer.echo(f"API key {key_id} not found.", err=True)
        sys.exit(1)


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
