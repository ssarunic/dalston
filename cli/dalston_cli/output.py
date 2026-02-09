"""Output formatting for Dalston CLI.

Provides consistent output formatting for both human-readable and
machine-readable (JSON) output modes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

if TYPE_CHECKING:
    from dalston_sdk import Job, JobSummary

# Console instances for stdout and stderr
console = Console()
error_console = Console(stderr=True)


def output_job_created(job: Job, as_json: bool = False) -> None:
    """Output job submission result.

    Args:
        job: Created job object.
        as_json: Output as JSON if True.
    """
    if as_json:
        console.print_json(
            data={
                "id": str(job.id),
                "status": job.status.value,
                "created_at": job.created_at.isoformat(),
            }
        )
    else:
        console.print(f"Job submitted: {job.id}")


def output_transcript(
    job: Job,
    fmt: str,
    output_path: str | None,
    include_speakers: bool = True,
    show_words: bool = False,
) -> None:
    """Output transcript in specified format.

    Args:
        job: Completed job with transcript.
        fmt: Output format (txt, json, srt, vtt).
        output_path: Path to write output, or None for stdout.
        include_speakers: Include speaker labels in output.
        show_words: Show word-level timestamps in text output.

    Raises:
        click.ClickException: If format is not supported.
    """
    import typer

    if fmt == "json":
        content = _job_to_json(job)
    elif fmt == "txt":
        if show_words:
            content = _format_words_text(job, include_speakers)
        else:
            content = job.transcript.text if job.transcript else ""
    elif fmt in ("srt", "vtt"):
        error_console.print(
            f"[red]Error:[/red] Format '{fmt}' requires the export endpoint. "
            f"Use 'dalston export {job.id} -f {fmt}' instead."
        )
        raise typer.Exit(code=1)
    else:
        error_console.print(f"[red]Error:[/red] Unsupported format: {fmt}")
        raise typer.Exit(code=1)

    if output_path:
        Path(output_path).write_text(content)
        error_console.print(f"Written to {output_path}")
    else:
        console.print(content)


def _job_to_json(job: Job) -> str:
    """Convert job to JSON string."""
    data: dict[str, Any] = {
        "id": str(job.id),
        "status": job.status.value,
        "created_at": job.created_at.isoformat(),
    }

    if job.started_at:
        data["started_at"] = job.started_at.isoformat()
    if job.completed_at:
        data["completed_at"] = job.completed_at.isoformat()
    if job.error:
        data["error"] = job.error

    if job.transcript:
        data["text"] = job.transcript.text
        data["language_code"] = job.transcript.language_code

        if job.transcript.words:
            data["words"] = [
                {
                    "text": w.text,
                    "start": w.start,
                    "end": w.end,
                    "confidence": w.confidence,
                    "speaker_id": w.speaker_id,
                }
                for w in job.transcript.words
            ]

        if job.transcript.segments:
            data["segments"] = [
                {
                    "id": s.id,
                    "text": s.text,
                    "start": s.start,
                    "end": s.end,
                    "speaker_id": s.speaker_id,
                    "words": (
                        [
                            {
                                "text": w.text,
                                "start": w.start,
                                "end": w.end,
                                "confidence": w.confidence,
                            }
                            for w in s.words
                        ]
                        if s.words
                        else None
                    ),
                }
                for s in job.transcript.segments
            ]

        if job.transcript.speakers:
            data["speakers"] = [
                {
                    "id": sp.id,
                    "label": sp.label,
                    "total_duration": sp.total_duration,
                }
                for sp in job.transcript.speakers
            ]

    return json.dumps(data, indent=2)


def _format_words_text(job: Job, include_speakers: bool = True) -> str:
    """Format transcript with word-level timestamps.

    Args:
        job: Job with transcript.
        include_speakers: Include speaker labels.

    Returns:
        Formatted text with word timestamps.
    """
    if not job.transcript:
        return ""

    lines: list[str] = []

    # If segments have words, format by segment
    if job.transcript.segments:
        for segment in job.transcript.segments:
            # Segment header with timestamp
            start_time = _format_timestamp(segment.start)
            end_time = _format_timestamp(segment.end)
            header = f"[{start_time} - {end_time}]"
            if include_speakers and segment.speaker_id:
                header += f" {segment.speaker_id}:"
            lines.append(header)

            # Words with timestamps
            if segment.words:
                word_parts = []
                for word in segment.words:
                    word_parts.append(f"{word.text}({word.start:.2f})")
                lines.append("  " + " ".join(word_parts))
            else:
                lines.append(f"  {segment.text}")
            lines.append("")  # Blank line between segments

    # Fallback to transcript-level words
    elif job.transcript.words:
        for word in job.transcript.words:
            speaker_prefix = ""
            if include_speakers and word.speaker_id:
                speaker_prefix = f"{word.speaker_id}: "
            lines.append(
                f"[{word.start:.2f}-{word.end:.2f}] {speaker_prefix}{word.text}"
            )

    # Fallback to plain text
    else:
        return job.transcript.text

    return "\n".join(lines)


def _format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS.ms."""
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins:02d}:{secs:05.2f}"


def wait_with_progress(
    client: Any, job_id: Any, quiet: bool = False, timeout: int | None = None
) -> Any:
    """Wait for job completion with progress display.

    Args:
        client: Dalston client instance.
        job_id: Job ID to wait for.
        quiet: Suppress progress output if True.
        timeout: Maximum time to wait in seconds, or None for no limit.

    Returns:
        Completed job.
    """
    if quiet:
        return client.wait_for_completion(job_id, timeout=timeout)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=error_console,
    ) as progress:
        task = progress.add_task("Processing...", total=None)

        def on_progress(pct: int, stage: str | None) -> None:
            desc = f"{stage or 'Processing'}... {pct}%"
            progress.update(task, description=desc)

        return client.wait_for_completion(
            job_id,
            poll_interval=1.0,
            timeout=timeout,
            on_progress=on_progress,
        )


def output_jobs_table(jobs: list[JobSummary], as_json: bool = False) -> None:
    """Display jobs list.

    Args:
        jobs: List of job summaries.
        as_json: Output as JSON if True.
    """
    if as_json:
        data = [
            {
                "id": str(j.id),
                "status": j.status.value,
                "created_at": j.created_at.isoformat(),
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                "progress": j.progress,
            }
            for j in jobs
        ]
        console.print_json(data=data)
        return

    table = Table()
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("Created")
    table.add_column("Progress")

    status_styles = {
        "completed": "green",
        "running": "yellow",
        "pending": "dim",
        "failed": "red",
        "cancelled": "red",
    }

    for job in jobs:
        status_style = status_styles.get(job.status.value, "")
        progress_str = f"{job.progress}%" if job.progress is not None else "-"

        table.add_row(
            str(job.id)[:12] + "...",
            f"[{status_style}]{job.status.value}[/]",
            job.created_at.strftime("%Y-%m-%d %H:%M"),
            progress_str,
        )

    console.print(table)


def output_job_detail(job: Job, as_json: bool = False) -> None:
    """Display job details.

    Args:
        job: Job to display.
        as_json: Output as JSON if True.
    """
    if as_json:
        console.print(_job_to_json(job))
        return

    console.print(f"ID:       {job.id}")
    console.print(f"Status:   {job.status.value}")
    console.print(f"Created:  {job.created_at}")

    if job.started_at:
        console.print(f"Started:  {job.started_at}")
    if job.completed_at:
        console.print(f"Completed: {job.completed_at}")
    if job.error:
        console.print(f"[red]Error:    {job.error}[/red]")
    if job.transcript and job.transcript.language_code:
        console.print(f"Language: {job.transcript.language_code}")


def format_duration(seconds: float) -> str:
    """Format duration as human-readable string.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted duration string.
    """
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


class LiveOutputHandler:
    """Handler for live human-readable output during real-time transcription."""

    def __init__(self, output_path: str | None = None, show_interim: bool = True):
        """Initialize handler.

        Args:
            output_path: Path to write transcript, or None for stdout only.
            show_interim: Show interim (partial) results.
        """
        self.output_path = output_path
        self.show_interim = show_interim
        self.file = open(output_path, "a") if output_path else None  # noqa: SIM115
        self._last_partial = ""
        self._closed = False

    def __enter__(self) -> LiveOutputHandler:
        """Enter context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager, ensuring file is closed."""
        self.close()

    def close(self) -> None:
        """Close the file handle if open."""
        if self.file and not self._closed:
            self.file.close()
            self._closed = True

    def partial(self, text: str, start: float) -> None:
        """Handle partial transcript.

        Args:
            text: Partial transcript text.
            start: Start timestamp.
        """
        if self.show_interim:
            # Clear previous partial, show new one
            clear = "\r" + " " * len(self._last_partial) + "\r"
            timestamp = f"[{self._format_time(start)}]"
            line = f"{timestamp} {text}..."
            sys.stderr.write(clear + line)
            sys.stderr.flush()
            self._last_partial = line

    def final(
        self, text: str, start: float, end: float, confidence: float | None
    ) -> None:
        """Handle final transcript.

        Args:
            text: Final transcript text.
            start: Start timestamp.
            end: End timestamp.
            confidence: Confidence score.
        """
        # Clear partial line
        if self._last_partial:
            sys.stderr.write("\r" + " " * len(self._last_partial) + "\r")
            self._last_partial = ""

        timestamp = f"[{self._format_time(start)}]"
        line = f"{timestamp} {text}"
        console.print(line)

        if self.file:
            self.file.write(text + "\n")
            self.file.flush()

    def session_end(
        self,
        duration: float,
        speech_duration: float,
        enhancement_job_id: str | None = None,
    ) -> None:
        """Handle session end.

        Args:
            duration: Total session duration.
            speech_duration: Total speech duration.
            enhancement_job_id: Enhancement job ID if triggered.
        """
        error_console.print(
            f"\n[Session ended: {duration:.1f}s total, {speech_duration:.1f}s speech]"
        )
        if enhancement_job_id:
            error_console.print(f"Enhancement job: {enhancement_job_id}")

        self.close()

    def _format_time(self, seconds: float) -> str:
        """Format timestamp as MM:SS."""
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"


class JsonlOutputHandler:
    """Handler for JSON Lines output during real-time transcription."""

    def __init__(self, output_path: str | None = None):
        """Initialize handler.

        Args:
            output_path: Path to write output, or None for stdout.
        """
        self.output_path = output_path
        self.file = open(output_path, "a") if output_path else sys.stdout  # noqa: SIM115
        self._closed = False

    def __enter__(self) -> JsonlOutputHandler:
        """Enter context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager, ensuring file is closed."""
        self.close()

    def close(self) -> None:
        """Close the file handle if open."""
        if self.file != sys.stdout and not self._closed:
            self.file.close()
            self._closed = True

    def partial(self, text: str, start: float) -> None:
        """Handle partial transcript (ignored in JSONL mode)."""
        pass

    def final(
        self, text: str, start: float, end: float, confidence: float | None
    ) -> None:
        """Handle final transcript.

        Args:
            text: Final transcript text.
            start: Start timestamp.
            end: End timestamp.
            confidence: Confidence score.
        """
        obj = {
            "type": "final",
            "text": text,
            "start": start,
            "end": end,
            "confidence": confidence,
        }
        self.file.write(json.dumps(obj) + "\n")
        self.file.flush()

    def session_end(
        self,
        duration: float,
        speech_duration: float,
        enhancement_job_id: str | None = None,
    ) -> None:
        """Handle session end.

        Args:
            duration: Total session duration.
            speech_duration: Total speech duration.
            enhancement_job_id: Enhancement job ID if triggered.
        """
        obj: dict[str, Any] = {
            "type": "session_end",
            "duration": duration,
            "speech_duration": speech_duration,
        }
        if enhancement_job_id:
            obj["enhancement_job_id"] = enhancement_job_id
        self.file.write(json.dumps(obj) + "\n")

        self.close()


class JsonOutputHandler:
    """Handler for full JSON output at session end."""

    def __init__(self, output_path: str | None = None):
        """Initialize handler.

        Args:
            output_path: Path to write output, or None for stdout.
        """
        self.output_path = output_path
        self.transcripts: list[dict[str, Any]] = []

    def partial(self, text: str, start: float) -> None:
        """Handle partial transcript (ignored)."""
        pass

    def final(
        self, text: str, start: float, end: float, confidence: float | None
    ) -> None:
        """Handle final transcript.

        Args:
            text: Final transcript text.
            start: Start timestamp.
            end: End timestamp.
            confidence: Confidence score.
        """
        self.transcripts.append(
            {
                "text": text,
                "start": start,
                "end": end,
                "confidence": confidence,
            }
        )

    def session_end(
        self,
        duration: float,
        speech_duration: float,
        enhancement_job_id: str | None = None,
    ) -> None:
        """Handle session end - output full JSON.

        Args:
            duration: Total session duration.
            speech_duration: Total speech duration.
            enhancement_job_id: Enhancement job ID if triggered.
        """
        data: dict[str, Any] = {
            "duration": duration,
            "speech_duration": speech_duration,
            "transcripts": self.transcripts,
        }
        if enhancement_job_id:
            data["enhancement_job_id"] = enhancement_job_id

        content = json.dumps(data, indent=2)

        if self.output_path:
            Path(self.output_path).write_text(content)
            error_console.print(f"Written to {self.output_path}")
        else:
            console.print(content)
