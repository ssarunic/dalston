"""Jobs command for managing transcription jobs."""

from __future__ import annotations

import click
from dalston_sdk import JobStatus

from dalston_cli.output import (
    console,
    error_console,
    output_job_detail,
    output_jobs_table,
    output_transcript,
    wait_with_progress,
)


@click.group()
def jobs() -> None:
    """Manage transcription jobs.

    List, view, and manage batch transcription jobs.
    """
    pass


@jobs.command("list")
@click.option(
    "--status",
    type=click.Choice(["pending", "running", "completed", "failed", "cancelled"]),
    help="Filter by job status.",
)
@click.option(
    "--limit",
    default=20,
    type=click.IntRange(1, 100),
    help="Maximum number of jobs to return.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
@click.pass_context
def list_jobs(
    ctx: click.Context,
    status: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """List transcription jobs.

    Shows recent jobs with their status and creation time.

    Examples:

        dalston jobs list

        dalston jobs list --status running

        dalston jobs list --limit 50 --json
    """
    client = ctx.obj["client"]

    # Convert status string to enum if provided
    status_filter = JobStatus(status) if status else None

    try:
        result = client.list_jobs(limit=limit, status=status_filter)
        output_jobs_table(result.jobs, as_json=as_json)
    except Exception as e:
        raise click.ClickException(f"Failed to list jobs: {e}") from e


@jobs.command("get")
@click.argument("job_id")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
@click.pass_context
def get_job(
    ctx: click.Context,
    job_id: str,
    as_json: bool,
) -> None:
    """Get job details.

    Shows detailed information about a specific job.

    Examples:

        dalston jobs get abc123

        dalston jobs get abc123 --json
    """
    client = ctx.obj["client"]

    try:
        job = client.get_job(job_id)
        output_job_detail(job, as_json=as_json)
    except Exception as e:
        raise click.ClickException(f"Failed to get job: {e}") from e


@jobs.command("wait")
@click.argument("job_id")
@click.option(
    "--timeout",
    default=300,
    type=int,
    help="Maximum time to wait in seconds.",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    default="txt",
    type=click.Choice(["txt", "json", "srt", "vtt"]),
    help="Output format for transcript.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path.",
)
@click.pass_context
def wait_job(
    ctx: click.Context,
    job_id: str,
    timeout: int,
    fmt: str,
    output: str | None,
) -> None:
    """Wait for job completion.

    Waits for a job to complete and outputs the transcript.

    Examples:

        dalston jobs wait abc123

        dalston jobs wait abc123 -f srt -o subtitles.srt
    """
    client = ctx.obj["client"]
    quiet = ctx.obj["quiet"]

    try:
        result = wait_with_progress(client, job_id, quiet, timeout=timeout)

        if result.status == JobStatus.FAILED:
            raise click.ClickException(f"Job failed: {result.error or 'Unknown error'}")
        elif result.status == JobStatus.CANCELLED:
            raise click.ClickException("Job was cancelled")

        output_transcript(result, fmt, output)
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Error waiting for job: {e}") from e


# Note: cancel command deferred - requires gateway endpoint
# @jobs.command("cancel")
# @click.argument("job_id")
# @click.pass_context
# def cancel_job(ctx: click.Context, job_id: str) -> None:
#     """Cancel a pending or running job."""
#     client = ctx.obj["client"]
#     client.cancel_job(job_id)
#     console.print(f"Job {job_id} cancelled")
