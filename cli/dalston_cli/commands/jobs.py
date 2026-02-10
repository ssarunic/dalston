"""Jobs command for managing transcription jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer
from dalston_sdk import JobStatus

from dalston_cli.main import state
from dalston_cli.output import (
    error_console,
    output_job_detail,
    output_jobs_table,
    output_transcript,
    wait_with_progress,
)

app = typer.Typer(help="Manage transcription jobs.")

StatusFilter = Literal["pending", "running", "completed", "failed", "cancelled"]
FormatType = Literal["txt", "json", "srt", "vtt"]


@app.command("list")
def list_jobs(
    status: Annotated[
        StatusFilter | None,
        typer.Option(
            help="Filter by job status.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option(
            min=1,
            max=100,
            help="Maximum number of jobs to return.",
        ),
    ] = 20,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """List transcription jobs.

    Shows recent jobs with their status and creation time.

    Examples:

        dalston jobs list

        dalston jobs list --status running

        dalston jobs list --limit 50 --json
    """
    client = state.client

    # Convert status string to enum if provided
    status_filter = JobStatus(status) if status else None

    try:
        result = client.list_jobs(limit=limit, status=status_filter)
        output_jobs_table(result.jobs, as_json=as_json)
    except Exception as e:
        error_console.print(f"[red]Error:[/red] Failed to list jobs: {e}")
        raise typer.Exit(code=1) from e


@app.command("get")
def get_job(
    job_id: Annotated[
        str,
        typer.Argument(help="Job ID to retrieve."),
    ],
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """Get job details.

    Shows detailed information about a specific job.

    Examples:

        dalston jobs get abc123

        dalston jobs get abc123 --json
    """
    client = state.client

    try:
        job = client.get_job(job_id)
        output_job_detail(job, as_json=as_json)
    except Exception as e:
        error_console.print(f"[red]Error:[/red] Failed to get job: {e}")
        raise typer.Exit(code=1) from e


@app.command("wait")
def wait_job(
    job_id: Annotated[
        str,
        typer.Argument(help="Job ID to wait for."),
    ],
    timeout: Annotated[
        int,
        typer.Option(
            help="Maximum time to wait in seconds.",
        ),
    ] = 300,
    fmt: Annotated[
        FormatType,
        typer.Option(
            "--format",
            "-f",
            help="Output format for transcript.",
        ),
    ] = "txt",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output file path.",
        ),
    ] = None,
) -> None:
    """Wait for job completion.

    Waits for a job to complete and outputs the transcript.

    Examples:

        dalston jobs wait abc123

        dalston jobs wait abc123 -f srt -o subtitles.srt
    """
    client = state.client
    quiet = state.quiet

    try:
        result = wait_with_progress(client, job_id, quiet, timeout=timeout)

        if result.status == JobStatus.FAILED:
            error_console.print(
                f"[red]Error:[/red] Job failed: {result.error or 'Unknown error'}"
            )
            raise typer.Exit(code=1)
        if result.status == JobStatus.CANCELLED:
            error_console.print("[red]Error:[/red] Job was cancelled")
            raise typer.Exit(code=1)

        output_path = str(output) if output else None
        output_transcript(result, fmt, output_path)
    except typer.Exit:
        raise
    except Exception as e:
        error_console.print(f"[red]Error:[/red] Error waiting for job: {e}")
        raise typer.Exit(code=1) from e


@app.command("cancel")
def cancel_job(
    job_id: Annotated[
        str,
        typer.Argument(help="Job ID to cancel."),
    ],
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """Cancel a pending or running job.

    Cancellation is "soft": running tasks complete naturally, only
    queued/pending work is cancelled.

    Examples:

        dalston jobs cancel abc123

        dalston jobs cancel abc123 --json
    """
    client = state.client

    try:
        job = client.cancel(job_id)

        if as_json:
            import json

            print(json.dumps({"id": str(job.id), "status": job.status.value}))
        else:
            from dalston_cli.output import console

            if job.status == JobStatus.CANCELLED:
                console.print(f"[green]Job {job_id} cancelled[/green]")
            else:
                console.print(
                    f"[yellow]Cancellation requested for {job_id} "
                    f"(status: {job.status.value})[/yellow]"
                )
    except Exception as e:
        error_console.print(f"[red]Error:[/red] Failed to cancel job: {e}")
        raise typer.Exit(code=1) from e
