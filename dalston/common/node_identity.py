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


@functools.lru_cache(maxsize=1)
def get_gpu_memory_total() -> str:
    """Probe total GPU VRAM at startup. Returns "0GB" if no GPU available."""
    # Try torch first (available in PyTorch-based engines)
    try:
        import torch

        if torch.cuda.is_available():
            total_gb = torch.cuda.get_device_properties(0).total_mem / 1e9
            return f"{total_gb:.1f}GB"
    except Exception:
        pass
    # Fallback to nvidia-smi (available in any container with --gpus)
    try:
        import subprocess

        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            total_mib = float(result.stdout.split("\n")[0])
            return f"{total_mib / 1024:.1f}GB"
    except Exception:
        pass
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
