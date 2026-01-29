"""Task DAG builder for job expansion.

Converts job parameters into a directed acyclic graph of tasks.
Each task represents a processing step executed by a specific engine.
"""

from uuid import UUID, uuid4

from dalston.common.models import Task, TaskStatus


def build_task_dag(job_id: UUID, audio_uri: str, parameters: dict) -> list[Task]:
    """Build a task DAG for a job.

    M01 stub implementation: Always creates a simple two-task pipeline:
        stub-transcriber -> stub-merger

    Args:
        job_id: The job's UUID
        audio_uri: S3 URI to the audio file
        parameters: Job parameters (unused in M01 stub)

    Returns:
        List of Task objects with dependencies wired
    """
    # Task 1: Stub transcriber (no dependencies)
    transcribe_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="transcribe",
        engine_id="stub-transcriber",
        status=TaskStatus.PENDING,
        dependencies=[],
        config={},
        input_uri=audio_uri,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )

    # Task 2: Stub merger (depends on transcriber)
    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id="stub-merger",
        status=TaskStatus.PENDING,
        dependencies=[transcribe_task.id],
        config={},
        input_uri=None,  # Will use previous task outputs
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )

    return [transcribe_task, merge_task]
