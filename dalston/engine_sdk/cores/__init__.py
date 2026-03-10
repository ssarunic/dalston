"""Runtime-specific shared inference cores.

Each core module encapsulates model loading and inference for a specific
ASR runtime. Both batch and realtime engine adapters delegate to the same
core instance so that a unified process shares one loaded model.
"""
