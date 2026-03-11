"""Runtime-specific shared inference cores.

Each core module encapsulates model loading and inference for a specific
ASR engine_id. Both batch and realtime engine adapters delegate to the same
core instance so that a unified process shares one loaded model.
"""
