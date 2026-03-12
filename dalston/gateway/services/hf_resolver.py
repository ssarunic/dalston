"""Resolve HuggingFace model metadata for engine routing.

This service enables automatic engine selection based on a HuggingFace model's
`library_name` field. When a user specifies a HuggingFace model ID (e.g.,
"nvidia/parakeet-tdt-1.1b"), we can fetch its model card and determine which
Dalston engine_id is appropriate.

Routing priority:
1. library_name (most reliable) - e.g., "ctranslate2" -> "faster-whisper"
2. Model tags (fallback) - e.g., "nemo" tag -> "nemo" engine_id
3. pipeline_tag (last resort) - "automatic-speech-recognition" -> "hf-asr"

Usage:
    resolver = HFResolver()
    engine_id = await resolver.resolve_engine_id("Systran/faster-whisper-large-v3")
    # Returns: "faster-whisper"
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

# Mapping from HuggingFace library_name to Dalston engine_id
# library_name is set in the model's config.json or model card
LIBRARY_TO_RUNTIME: dict[str, str] = {
    # CTranslate2-based models (faster-whisper)
    "ctranslate2": "faster-whisper",
    # NVIDIA NeMo models
    "nemo": "nemo",
    "nemo-asr": "nemo",
    # HuggingFace Transformers (generic ASR pipeline)
    "transformers": "hf-asr",
    # vLLM-based audio LLMs (Whisper via vLLM, etc.)
    "vllm": "vllm-asr",
    # Whisper.cpp models
    "whisper.cpp": "whisper-cpp",
    # OpenAI Whisper (original implementation)
    "whisper": "whisper",
}

# Fallback mapping from model tags to engine_id
# Used when library_name is not set
TAG_TO_RUNTIME: dict[str, str] = {
    # Explicit engine_id tags
    "faster-whisper": "faster-whisper",
    "ctranslate2": "faster-whisper",
    "nemo": "nemo",
    "whisper": "faster-whisper",  # Default Whisper models to faster-whisper
    "whisper.cpp": "whisper-cpp",
}

# Pipeline tags that indicate ASR capability
ASR_PIPELINE_TAGS = frozenset(
    {
        "automatic-speech-recognition",
        "audio-to-text",
        "speech-recognition",
    }
)


@dataclass
class HFModelMetadata:
    """Extracted metadata from HuggingFace model card."""

    model_id: str
    library_name: str | None
    pipeline_tag: str | None
    tags: list[str]
    languages: list[str]
    downloads: int
    likes: int
    # Computed engine_id from card routing
    resolved_engine_id: str | None


class HFResolver:
    """Resolve HuggingFace model metadata for engine routing.

    This service fetches model info from HuggingFace Hub and determines
    which Dalston engine_id can load the model based on its library_name,
    tags, and pipeline_tag.
    """

    def __init__(self) -> None:
        """Initialize the resolver.

        The HfApi client is created lazily to avoid import overhead
        when the service is not used.
        """
        self._api: Any = None

    @property
    def api(self) -> Any:
        """Lazy-loaded HuggingFace API client."""
        if self._api is None:
            from huggingface_hub import HfApi

            self._api = HfApi()
        return self._api

    async def get_model_info(self, model_id: str) -> Any:
        """Fetch model info from HuggingFace Hub.

        Args:
            model_id: HuggingFace model ID (e.g., "nvidia/parakeet-tdt-1.1b")

        Returns:
            ModelInfo object if found, None if the model doesn't exist
            or the API call fails.
        """
        try:
            return await asyncio.to_thread(self.api.model_info, model_id)
        except Exception as e:
            logger.warning(
                "hf_model_info_failed",
                model_id=model_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

    async def get_model_total_size_bytes(self, model_id: str) -> int | None:
        """Estimate total repository size for a HuggingFace model.

        Args:
            model_id: HuggingFace model ID

        Returns:
            Sum of sibling file sizes in bytes when available, otherwise None.
        """
        try:
            info = await asyncio.to_thread(
                self.api.model_info,
                model_id,
                files_metadata=True,
            )
        except Exception as e:
            logger.warning(
                "hf_model_size_lookup_failed",
                model_id=model_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

        siblings = getattr(info, "siblings", None) or []
        sizes = [getattr(sibling, "size", None) for sibling in siblings]
        known_sizes = [size for size in sizes if isinstance(size, int) and size >= 0]

        if not known_sizes:
            return None

        return sum(known_sizes)

    async def resolve_engine_id(self, model_id: str) -> str | None:
        """Determine which engine_id can load a HuggingFace model.

        Routing priority:
        1. library_name (most reliable)
        2. Model tags (fallback)
        3. pipeline_tag (last resort for generic ASR)

        Args:
            model_id: HuggingFace model ID (e.g., "Systran/faster-whisper-large-v3")

        Returns:
            Dalston engine_id name (e.g., "faster-whisper", "nemo") or None
            if the engine_id cannot be determined.
        """
        info = await self.get_model_info(model_id)
        if info is None:
            return None

        # Get pipeline_tag early - needed to validate generic libraries
        pipeline_tag = getattr(info, "pipeline_tag", None)
        pipeline_tag_lower = pipeline_tag.lower() if pipeline_tag else None

        # 1. Check library_name (most reliable for ASR-specific libraries)
        library_name = getattr(info, "library_name", None)
        if library_name:
            library_name_lower = library_name.lower()
            engine_id = LIBRARY_TO_RUNTIME.get(library_name_lower)
            if engine_id:
                # Special case: "transformers" is used for many tasks (LLM, ASR, etc.)
                # Only route to hf-asr if pipeline_tag confirms it's ASR
                if library_name_lower == "transformers":
                    if pipeline_tag_lower not in ASR_PIPELINE_TAGS:
                        logger.debug(
                            "skipping_transformers_not_asr",
                            model_id=model_id,
                            pipeline_tag=pipeline_tag,
                        )
                        # Fall through to other checks
                    else:
                        logger.info(
                            "runtime_resolved_by_library",
                            model_id=model_id,
                            library_name=library_name,
                            pipeline_tag=pipeline_tag,
                            engine_id=engine_id,
                        )
                        return engine_id
                else:
                    logger.info(
                        "runtime_resolved_by_library",
                        model_id=model_id,
                        library_name=library_name,
                        engine_id=engine_id,
                    )
                    return engine_id

        # 2. Check tags as fallback
        tags = set(info.tags) if info.tags else set()
        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower in TAG_TO_RUNTIME:
                engine_id = TAG_TO_RUNTIME[tag_lower]
                logger.info(
                    "runtime_resolved_by_tag",
                    model_id=model_id,
                    tag=tag,
                    engine_id=engine_id,
                )
                return engine_id

        # 3. Check pipeline_tag for generic ASR
        pipeline_tag = getattr(info, "pipeline_tag", None)
        if pipeline_tag and pipeline_tag.lower() in ASR_PIPELINE_TAGS:
            # Default to HuggingFace transformers pipeline for generic ASR
            logger.info(
                "runtime_resolved_by_pipeline_tag",
                model_id=model_id,
                pipeline_tag=pipeline_tag,
                engine_id="hf-asr",
            )
            return "hf-asr"

        logger.warning(
            "runtime_not_resolved",
            model_id=model_id,
            library_name=library_name,
            pipeline_tag=pipeline_tag,
            tags=list(tags)[:10],  # Limit tags in log
        )
        return None

    async def get_model_metadata(self, model_id: str) -> HFModelMetadata | None:
        """Get full metadata for a model, including resolved engine_id.

        This method is useful for caching model metadata in the registry
        after successful resolution.

        Args:
            model_id: HuggingFace model ID

        Returns:
            HFModelMetadata with all extracted fields, or None if the
            model doesn't exist.
        """
        info = await self.get_model_info(model_id)
        if info is None:
            return None

        # Extract languages (can be string or list)
        languages_raw = getattr(info, "language", None)
        if isinstance(languages_raw, str):
            languages = [languages_raw]
        elif isinstance(languages_raw, list):
            languages = languages_raw
        else:
            languages = []

        # Resolve engine_id
        engine_id = await self.resolve_engine_id(model_id)

        return HFModelMetadata(
            model_id=model_id,
            library_name=getattr(info, "library_name", None),
            pipeline_tag=getattr(info, "pipeline_tag", None),
            tags=list(info.tags) if info.tags else [],
            languages=languages,
            downloads=getattr(info, "downloads", 0),
            likes=getattr(info, "likes", 0),
            resolved_engine_id=engine_id,
        )

    def get_library_to_engine_id_mapping(self) -> dict[str, str]:
        """Return the library_name to engine_id mapping for reference.

        Useful for debugging and API responses.
        """
        return dict(LIBRARY_TO_RUNTIME)

    def get_tag_to_engine_id_mapping(self) -> dict[str, str]:
        """Return the tag to engine_id fallback mapping for reference."""
        return dict(TAG_TO_RUNTIME)

    def get_supported_engine_ids(self) -> list[str]:
        """Return list of all engine_ids that can be resolved."""
        engine_ids = set(LIBRARY_TO_RUNTIME.values())
        engine_ids.update(TAG_TO_RUNTIME.values())
        engine_ids.add("hf-asr")  # Fallback engine_id
        return sorted(engine_ids)
