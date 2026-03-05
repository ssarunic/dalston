"""M52 tests for SDK public surface cleanup."""

from __future__ import annotations

import dalston.engine_sdk as engine_sdk
from dalston.engine_sdk import types as sdk_types


def test_no_legacy_task_aliases_in_types_module() -> None:
    assert not hasattr(sdk_types, "TaskInput")
    assert not hasattr(sdk_types, "TaskOutput")


def test_no_legacy_task_aliases_in_public_exports() -> None:
    assert "TaskInput" not in engine_sdk.__all__
    assert "TaskOutput" not in engine_sdk.__all__
