"""Dalston Gateway CLI commands.

Usage:
    python -m dalston.gateway.cli create-key --name "My Key"
    python -m dalston.gateway.cli create-key --name "Admin" --scopes admin
    python -m dalston.gateway.cli list-keys
    python -m dalston.gateway.cli revoke-key <key-id>
"""

import asyncio
import sys
from typing import Annotated

import typer

from dalston.db.session import DEFAULT_TENANT_ID
from dalston.gateway.services.auth import DEFAULT_SCOPES, AuthService, Scope

app = typer.Typer(help="Dalston Gateway CLI.")

# Valid scope values for CLI help
VALID_SCOPES = [s.value for s in Scope]


async def _create_key(name: str, scopes: list[Scope]) -> tuple[str, str, list[Scope]]:
    """Create an API key with specified scopes.

    Args:
        name: Human-readable name for the key
        scopes: List of permission scopes

    Returns:
        Tuple of (raw_key, key_id, scopes)
    """
    from dalston.common.redis import get_redis
    from dalston.db.session import async_session, init_db

    # Initialize database (creates tables if needed)
    await init_db()

    redis = await get_redis()
    async with async_session() as db:
        auth_service = AuthService(db, redis)

        raw_key, api_key = await auth_service.create_api_key(
            name=name,
            tenant_id=DEFAULT_TENANT_ID,
            scopes=scopes,
            rate_limit=None,
        )

        return raw_key, str(api_key.id), api_key.scopes


def parse_scopes(scopes_str: str | None) -> list[Scope]:
    """Parse comma-separated scopes string into Scope list.

    Args:
        scopes_str: Comma-separated scopes (e.g., "jobs:read,jobs:write,admin")

    Returns:
        List of Scope enums

    Raises:
        typer.BadParameter: If invalid scope provided
    """
    if not scopes_str:
        return list(DEFAULT_SCOPES)

    scopes = []
    for s in scopes_str.split(","):
        s = s.strip()
        try:
            scopes.append(Scope(s))
        except ValueError:
            raise typer.BadParameter(
                f"Invalid scope '{s}'. Valid scopes: {', '.join(VALID_SCOPES)}"
            ) from None

    return scopes


@app.command("create-key")
def create_key(
    name: Annotated[
        str,
        typer.Option(
            "--name",
            "-n",
            help="Human-readable name for the API key",
        ),
    ] = "API Key",
    scopes: Annotated[
        str | None,
        typer.Option(
            "--scopes",
            "-s",
            help=f"Comma-separated scopes. Valid: {', '.join(VALID_SCOPES)}. "
            f"Default: {', '.join(s.value for s in DEFAULT_SCOPES)}",
        ),
    ] = None,
) -> None:
    """Create an API key with specified scopes.

    The key is displayed once and cannot be retrieved later.
    Store it securely!

    Examples:
        # Default scopes (jobs:read, jobs:write, realtime)
        python -m dalston.gateway.cli create-key --name "My Key"

        # Admin key (full access)
        python -m dalston.gateway.cli create-key --name "Admin" --scopes admin

        # Read-only key
        python -m dalston.gateway.cli create-key --name "Reader" --scopes jobs:read

        # Multiple scopes
        python -m dalston.gateway.cli create-key --name "Worker" --scopes jobs:read,jobs:write
    """
    try:
        parsed_scopes = parse_scopes(scopes)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    scope_names = ", ".join(s.value for s in parsed_scopes)
    typer.echo(f"Creating API key '{name}' with scopes: {scope_names}...")

    try:
        raw_key, key_id, actual_scopes = asyncio.run(_create_key(name, parsed_scopes))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    typer.echo()
    typer.echo("=" * 60)
    typer.echo("API key created successfully!")
    typer.echo("=" * 60)
    typer.echo()
    typer.echo(f"Key ID:  {key_id}")
    typer.echo(f"API Key: {raw_key}")
    typer.echo(f"Scopes:  {', '.join(s.value for s in actual_scopes)}")
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
def list_keys(
    include_revoked: Annotated[
        bool,
        typer.Option(
            "--include-revoked",
            "-r",
            help="Include revoked keys in the list",
        ),
    ] = False,
) -> None:
    """List all API keys for the default tenant."""

    async def _list_keys():
        from dalston.common.redis import get_redis
        from dalston.db.session import async_session

        redis = await get_redis()
        async with async_session() as db:
            auth_service = AuthService(db, redis)
            return await auth_service.list_api_keys(
                DEFAULT_TENANT_ID,
                include_revoked=include_revoked,
            )

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
        status = " [REVOKED]" if key.is_revoked else ""
        typer.echo(f"  ID:      {key.id}{status}")
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
        from dalston.db.session import async_session

        redis = await get_redis()
        async with async_session() as db:
            auth_service = AuthService(db, redis)
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
