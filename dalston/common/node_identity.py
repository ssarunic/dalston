"""Node identity detection for infrastructure topology.

Collects host metadata at engine startup and caches the result.
Called once by both batch and realtime engine runners before registration.

Detection priority:
1. DALSTON_DEPLOY_ENV env var — explicit override ("aws" or "local")
2. IMDSv2 probe — if reachable, we're on EC2
3. Fallback — local dev
"""

from __future__ import annotations

import functools
import json
import os
import socket
import subprocess
import types
import urllib.request
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

IMDS_BASE = "http://169.254.169.254"
IMDS_TOKEN_URL = f"{IMDS_BASE}/latest/api/token"
IMDS_IDENTITY_URL = f"{IMDS_BASE}/latest/dynamic/instance-identity/document"
IMDS_TIMEOUT = 0.5  # seconds — bounds startup delay on non-EC2


@dataclass(frozen=True)
class NodeIdentity:
    """Identity of the host running an engine process."""

    hostname: str
    node_id: str  # EC2 instance ID, or hostname as fallback
    deploy_env: str  # "aws" | "local"
    region: str | None  # AWS AZ, or None on local
    instance_type: str | None  # e.g. "g4dn.xlarge", or None on local


def _probe_imds() -> dict | None:
    """Probe EC2 IMDSv2 for instance metadata.

    Returns parsed identity document dict on success, None on failure.
    Total timeout on non-EC2 hosts is bounded by IMDS_TIMEOUT.
    Uses urllib (stdlib) to avoid requiring httpx in the engine base image.
    """
    try:
        # Step 1: Get IMDSv2 token
        token_req = urllib.request.Request(
            IMDS_TOKEN_URL,
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
        )
        with urllib.request.urlopen(token_req, timeout=IMDS_TIMEOUT) as resp:
            token = resp.read().decode()

        # Step 2: Get instance identity document
        identity_req = urllib.request.Request(
            IMDS_IDENTITY_URL,
            headers={"X-aws-ec2-metadata-token": token},
        )
        with urllib.request.urlopen(identity_req, timeout=IMDS_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


_nvidia_smi_available: bool | None = None


def _query_nvidia_smi_gb(field: str) -> str | None:
    """Query nvidia-smi for a memory field (MiB) and return as 'X.YGB'."""
    global _nvidia_smi_available
    if _nvidia_smi_available is False:
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            _nvidia_smi_available = True
            mib = float(result.stdout.split("\n")[0])
            return f"{mib / 1024:.1f}GB"
    except FileNotFoundError:
        _nvidia_smi_available = False
    except Exception:
        pass
    return None


@functools.lru_cache(maxsize=1)
def _get_torch() -> types.ModuleType | None:
    """Return the torch module if CUDA is available, else None. Probed once."""

    try:
        import torch

        if torch.cuda.is_available():
            return torch
    except Exception:
        pass
    return None


def get_gpu_memory_used() -> str:
    """Probe current GPU VRAM usage. Returns "0GB" if no GPU available."""
    smi = _query_nvidia_smi_gb("memory.used")
    if smi:
        return smi
    torch = _get_torch()
    if torch is not None:
        return f"{torch.cuda.memory_allocated() / 1e9:.1f}GB"
    return "0GB"


@functools.lru_cache(maxsize=1)
def get_gpu_memory_total() -> str:
    """Probe total GPU VRAM at startup. Returns "0GB" if no GPU available.

    Prefers nvidia-smi over torch to avoid initializing CUDA in the parent
    process — engines that fork (e.g. vLLM) crash if CUDA is already initialized.
    """
    smi = _query_nvidia_smi_gb("memory.total")
    if smi:
        return smi
    torch = _get_torch()
    if torch is not None:
        return f"{torch.cuda.get_device_properties(0).total_memory / 1e9:.1f}GB"
    return "0GB"


@functools.lru_cache(maxsize=1)
def detect_node_identity() -> NodeIdentity:
    """Detect node identity once at startup. Results are cached."""
    hostname = socket.gethostname()
    # DALSTON_HOST_HOSTNAME is the host machine's real hostname, injected
    # by docker-compose. In containers, socket.gethostname() returns the
    # container ID, so this env var is needed to group co-located engines.
    host_hostname = os.environ.get("DALSTON_HOST_HOSTNAME")
    deploy_env_override = os.environ.get("DALSTON_DEPLOY_ENV")

    # If explicitly set to local, skip IMDS probe entirely
    if deploy_env_override and deploy_env_override != "aws":
        logger.info(
            "node_identity_detected",
            deploy_env=deploy_env_override,
            hostname=hostname,
            node_id=host_hostname or hostname,
        )
        return NodeIdentity(
            hostname=hostname,
            node_id=host_hostname or hostname,
            deploy_env=deploy_env_override,
            region=None,
            instance_type=None,
        )

    # Probe IMDSv2 (either forced via DALSTON_DEPLOY_ENV=aws or auto-detect)
    identity = _probe_imds()
    if identity is not None:
        node_id = identity.get("instanceId", hostname)
        region = identity.get("availabilityZone")
        instance_type = identity.get("instanceType")
        logger.info(
            "node_identity_detected",
            deploy_env="aws",
            node_id=node_id,
            region=region,
            instance_type=instance_type,
        )
        return NodeIdentity(
            hostname=hostname,
            node_id=node_id,
            deploy_env="aws",
            region=region,
            instance_type=instance_type,
        )

    # Fallback: local dev
    node_id = host_hostname or hostname
    logger.info(
        "node_identity_detected", deploy_env="local", hostname=hostname, node_id=node_id
    )
    return NodeIdentity(
        hostname=hostname,
        node_id=node_id,
        deploy_env="local",
        region=None,
        instance_type=None,
    )
