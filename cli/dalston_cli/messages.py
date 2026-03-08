"""Centralized user-facing strings for the Dalston CLI.

Usage:

    from dalston_cli.messages import CLIMsg

    error_console.print(CLIMsg.ERR_NO_INPUT)

All user-facing CLI strings live here. Keep them alphabetized within
each section for easy scanning.
"""


class CLIMsg:
    """CLI message constants.

    Organized by category, then alphabetically.

    NOTE: Constants prefixed with ``ERR_`` and ``BOOTSTRAP_FAILED`` /
    ``BOOTSTRAP_HOW_TO_FIX`` embed Rich console markup (e.g. ``[red]...[/red]``).
    They must only be used with ``rich.console.Console.print()``; passing them
    to plain loggers or JSON serializers will leak the markup tags.
    """

    # -------------------------------------------------------------------------
    # Bootstrap status messages
    # -------------------------------------------------------------------------
    BOOTSTRAP_DISABLED_VALIDATING = (
        "Bootstrap: disabled, validating manual prerequisites"
    )
    BOOTSTRAP_ENSURING_MODEL = "Bootstrap: ensuring model '{model}'"
    BOOTSTRAP_ENSURING_SERVER = "Bootstrap: ensuring local server"
    BOOTSTRAP_FAILED = "[red]Bootstrap failed:[/red] {error}"
    BOOTSTRAP_HOW_TO_FIX = "[yellow]How to fix:[/yellow] {remediation}"
    BOOTSTRAP_PREFLIGHT = "Bootstrap: preflight checks"
    BOOTSTRAP_SERVER_SIDE_AUTO = (
        "Bootstrap: using server-side model auto-selection"
    )

    # -------------------------------------------------------------------------
    # Bootstrap error messages
    # -------------------------------------------------------------------------
    MODEL_NOT_READY = (
        "Model '{model_id}' is not ready while DALSTON_BOOTSTRAP=false{error_detail}"
    )
    MODEL_NOT_READY_REMEDIATION = (
        "Run `dalston models pull {model_id}` and retry, or set "
        "DALSTON_BOOTSTRAP=true."
    )
    SERVER_NOT_HEALTHY = (
        "Local server is not healthy while DALSTON_BOOTSTRAP=false."
    )
    SERVER_NOT_HEALTHY_REMEDIATION = (
        "Run `dalston status` for diagnostics, or set DALSTON_BOOTSTRAP=true "
        "for automatic recovery."
    )
    SERVER_NOT_REACHABLE = (
        "Local server is not reachable while DALSTON_BOOTSTRAP=false."
    )
    SERVER_NOT_REACHABLE_REMEDIATION = (
        "Start the server manually or set DALSTON_BOOTSTRAP=true for "
        "automatic local startup."
    )

    # -------------------------------------------------------------------------
    # Input validation errors
    # -------------------------------------------------------------------------
    ERR_FILES_AND_URL = (
        "[red]Error:[/red] Provide either audio files or --url, not both."
    )
    ERR_INVALID_LITE_PROFILE = "[red]Error:[/red] Invalid lite profile: {error}"
    ERR_NO_INPUT = (
        "[red]Error:[/red] Either provide audio files or --url, not neither."
    )

    # -------------------------------------------------------------------------
    # Transcription progress / result messages
    # -------------------------------------------------------------------------
    ERR_PROCESSING = "[red]Error:[/red] Error processing {source}: {error}"
    ERR_TRANSCRIPTION_FAILED = (
        "[red]Error:[/red] Transcription failed: {error}"
    )
    SUBMITTING_FILE = "Submitting: {file_path}"
    SUBMITTING_URL = "Submitting URL: {url}..."
