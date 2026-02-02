"""Dalston Gateway CLI commands.

Usage:
    python -m dalston.gateway.cli create-admin-key --name "Admin"
"""

import asyncio
import sys

import click

from dalston.db.session import DEFAULT_TENANT_ID
from dalston.gateway.services.auth import AuthService, Scope


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


@click.group()
def cli():
    """Dalston Gateway CLI."""
    pass


@cli.command("create-admin-key")
@click.option(
    "--name",
    "-n",
    default="Admin",
    help="Human-readable name for the API key",
)
def create_admin_key(name: str):
    """Create an admin API key for the default tenant.

    The key is displayed once and cannot be retrieved later.
    Store it securely!

    Example:
        python -m dalston.gateway.cli create-admin-key --name "My Admin Key"
    """
    click.echo(f"Creating admin API key '{name}'...")

    try:
        raw_key, key_id = asyncio.run(_create_admin_key(name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo()
    click.echo("=" * 60)
    click.echo("Admin API key created successfully!")
    click.echo("=" * 60)
    click.echo()
    click.echo(f"Key ID:  {key_id}")
    click.echo(f"API Key: {raw_key}")
    click.echo()
    click.echo("IMPORTANT: Store this key securely!")
    click.echo("It cannot be retrieved later.")
    click.echo()
    click.echo("=" * 60)
    click.echo("Usage Examples")
    click.echo("=" * 60)
    click.echo()
    click.echo("# Set as environment variable")
    click.echo(f'export DALSTON_API_KEY="{raw_key}"')
    click.echo()
    click.echo("# Use with curl")
    click.echo('curl -X POST http://localhost:8000/v1/audio/transcriptions \\')
    click.echo(f'  -H "Authorization: Bearer {raw_key}" \\')
    click.echo('  -F "file=@audio.mp3"')
    click.echo()
    click.echo("# Use with dalston-cli")
    click.echo(f'dalston --api-key "{raw_key}" transcribe audio.mp3')
    click.echo()


@cli.command("list-keys")
def list_keys():
    """List all API keys for the default tenant."""

    async def _list_keys():
        from dalston.common.redis import get_redis

        redis = await get_redis()
        auth_service = AuthService(redis)
        return await auth_service.list_api_keys(DEFAULT_TENANT_ID)

    try:
        keys = asyncio.run(_list_keys())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not keys:
        click.echo("No API keys found.")
        return

    click.echo(f"Found {len(keys)} API key(s):")
    click.echo()

    for key in keys:
        click.echo(f"  ID:      {key.id}")
        click.echo(f"  Prefix:  {key.prefix}...")
        click.echo(f"  Name:    {key.name}")
        click.echo(f"  Scopes:  {', '.join(s.value for s in key.scopes)}")
        click.echo(f"  Created: {key.created_at.isoformat()}")
        if key.last_used_at:
            click.echo(f"  Used:    {key.last_used_at.isoformat()}")
        click.echo()


@cli.command("revoke-key")
@click.argument("key_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def revoke_key(key_id: str, yes: bool):
    """Revoke an API key by ID."""
    from uuid import UUID

    try:
        key_uuid = UUID(key_id)
    except ValueError:
        click.echo(f"Error: Invalid key ID format: {key_id}", err=True)
        sys.exit(1)

    if not yes:
        click.confirm(f"Revoke API key {key_id}?", abort=True)

    async def _revoke_key():
        from dalston.common.redis import get_redis

        redis = await get_redis()
        auth_service = AuthService(redis)
        return await auth_service.revoke_api_key(key_uuid)

    try:
        success = asyncio.run(_revoke_key())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if success:
        click.echo(f"API key {key_id} revoked.")
    else:
        click.echo(f"API key {key_id} not found.", err=True)
        sys.exit(1)


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
