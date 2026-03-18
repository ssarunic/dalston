"""Generic composite engine entrypoint.

Bootstraps a :class:`~dalston.engine_sdk.base_composite.CompositeEngine`
directly from ``engine.yaml`` — no ``engine.py`` required.

Usage (container CMD)::

    CMD ["python", "-m", "dalston.engine_sdk.composite_runner"]

:class:`CompositeEngine` reads ``/etc/dalston/engine.yaml`` (container) or
``./engine.yaml`` (local dev) and resolves all children, stages, and pipeline
config from the ``compose`` block.  There is nothing composite-engine-specific
to put in a subclass, so none is needed.

Unlike leaf engines, composite engines have no Redis queue to poll — they
receive work exclusively via HTTP and dispatch to child engines.  The runner
therefore starts only the HTTP server, bypassing :class:`EngineRunner`.
"""

from __future__ import annotations

import asyncio
import os

from dalston.engine_sdk.base_composite import CompositeEngine


def main() -> None:
    port = int(
        os.environ.get(
            "DALSTON_METRICS_PORT", os.environ.get("DALSTON_HTTP_PORT", "9100")
        )
    )
    engine = CompositeEngine()
    http_server = engine.create_http_server(port=port)
    asyncio.run(http_server.serve())


if __name__ == "__main__":
    main()
