from __future__ import annotations

import importlib
import sys


def test_orchestrator_package_defers_engine_selector_import():
    stale_modules = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "dalston.orchestrator" or name.startswith("dalston.orchestrator.")
    }
    try:
        orchestrator = importlib.import_module("dalston.orchestrator")

        assert "dalston.orchestrator.engine_selector" not in sys.modules

        error_cls = orchestrator.NoCapableEngineError

        assert error_cls.__name__ == "NoCapableEngineError"
        assert "dalston.orchestrator.engine_selector" in sys.modules
    finally:
        for name in list(sys.modules):
            if name == "dalston.orchestrator" or name.startswith(
                "dalston.orchestrator."
            ):
                sys.modules.pop(name, None)
        sys.modules.update(stale_modules)


def test_orchestrator_package_allows_lazy_submodule_imports():
    stale_modules = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "dalston.orchestrator" or name.startswith("dalston.orchestrator.")
    }
    try:
        orchestrator = importlib.import_module("dalston.orchestrator")

        distributed_main = orchestrator.distributed_main

        assert distributed_main.__name__ == "dalston.orchestrator.distributed_main"
    finally:
        for name in list(sys.modules):
            if name == "dalston.orchestrator" or name.startswith(
                "dalston.orchestrator."
            ):
                sys.modules.pop(name, None)
        sys.modules.update(stale_modules)
