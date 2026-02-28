#!/usr/bin/env python3
"""Validate that generated catalog entries are represented in docker-compose."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}"
        )
    return result.stdout


def load_compose_services(compose_file: str) -> dict:
    profiles_output = run_command(
        ["docker", "compose", "-f", compose_file, "config", "--profiles"]
    )
    profiles = [line.strip() for line in profiles_output.splitlines() if line.strip()]

    cmd = ["docker", "compose", "-f", compose_file]
    for profile in profiles:
        cmd.extend(["--profile", profile])
    cmd.extend(["config", "--format", "json"])

    raw = run_command(cmd)
    parsed = json.loads(raw)
    return parsed.get("services", {})


def parse_environment(env: object) -> dict[str, str]:
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}

    if isinstance(env, list):
        parsed: dict[str, str] = {}
        for item in env:
            if not isinstance(item, str):
                continue
            if "=" in item:
                key, value = item.split("=", 1)
                parsed[key] = value
        return parsed

    return {}


def collect_batch_ids(services: dict) -> set[str]:
    batch_ids: set[str] = set()
    for service_name, service in services.items():
        if not service_name.startswith("stt-batch-"):
            continue
        env = parse_environment(service.get("environment"))
        engine_id = env.get("DALSTON_ENGINE_ID")
        if engine_id:
            batch_ids.add(engine_id)
    return batch_ids


def collect_realtime_ids(services: dict) -> set[str]:
    rt_ids: set[str] = set()
    prefix = "stt-rt-transcribe-"
    for service_name in services:
        if not service_name.startswith(prefix):
            continue
        engine_id = service_name[len(prefix) :]
        if engine_id.endswith("-cpu"):
            engine_id = engine_id[: -len("-cpu")]
        rt_ids.add(engine_id)
    return rt_ids


def main() -> int:
    catalog_path = Path("dalston/orchestrator/generated_catalog.json")
    if not catalog_path.exists():
        print(f"Catalog file not found: {catalog_path}", file=sys.stderr)
        return 1

    services = load_compose_services("docker-compose.yml")
    batch_ids = collect_batch_ids(services)
    realtime_ids = collect_realtime_ids(services)

    catalog = json.loads(catalog_path.read_text())
    engines = catalog.get("engines", {})

    missing: list[str] = []
    for entry in engines.values():
        engine_id = entry.get("id")
        if not engine_id:
            continue

        if entry.get("type") == "realtime":
            if engine_id not in realtime_ids:
                missing.append(f"realtime:{engine_id}")
        else:
            if engine_id not in batch_ids:
                missing.append(f"batch:{engine_id}")

    if missing:
        print("Catalog entries missing from docker-compose mapping:")
        for item in sorted(missing):
            print(f"  - {item}")
        return 1

    print(
        "Catalog/compose mapping valid "
        f"(batch={len(batch_ids)}, realtime={len(realtime_ids)}, catalog={len(engines)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
