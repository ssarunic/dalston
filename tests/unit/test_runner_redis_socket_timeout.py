"""The engine runner's Redis read timeout must outlast its blocking poll.

redis-py 8 changed the default ``socket_timeout`` from ``None`` to 5 s.
The runner polls with ``XREADGROUP ... BLOCK STREAM_POLL_TIMEOUT``, so a
shorter read timeout raises ``TimeoutError("Timeout reading from socket")``
on every idle poll (seen on every engine in production, 2026-09-03).
"""

import os
from unittest.mock import MagicMock, patch

from dalston.engine_sdk.base import BatchTaskContext, Engine
from dalston.engine_sdk.runner import EngineRunner
from dalston.engine_sdk.types import TaskRequest, TaskResponse


class _NoopEngine(Engine):
    def process(self, input: TaskRequest, ctx: BatchTaskContext) -> TaskResponse:
        del input, ctx
        return TaskResponse(data={})


def test_runner_redis_socket_timeout_outlasts_blocking_poll() -> None:
    with patch.dict(os.environ, {"DALSTON_ENGINE_ID": "socket-timeout-test"}):
        runner = EngineRunner(_NoopEngine())

    with patch("dalston.engine_sdk.runner.redis.from_url") as from_url:
        from_url.return_value = MagicMock()
        client = runner.redis_client
        assert client is runner.redis_client  # cached, one client per runner

    from_url.assert_called_once()
    kwargs = from_url.call_args.kwargs
    assert kwargs["socket_timeout"] > EngineRunner.STREAM_POLL_TIMEOUT
    assert kwargs["socket_connect_timeout"] > 0
    assert kwargs["decode_responses"] is True
