"""Wav2Vec2 alignment model loading for CTC forced alignment.

Supports two model sources:
    1. **torchaudio pipelines** — bundled wav2vec2 models for major European
       languages (en, fr, de, es, it). Loaded via torchaudio.pipelines.
    2. **HuggingFace Hub** — community wav2vec2 models for 30+ additional
       languages. Loaded via transformers Wav2Vec2ForCTC / Wav2Vec2Processor.

Each model provides a character dictionary mapping lowercase characters to
CTC token indices, used by the forced alignment algorithm.
"""

from __future__ import annotations

import logging
from typing import Any

import torchaudio
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Default language → model mappings
# -----------------------------------------------------------------------

# torchaudio bundled models (faster to load, no HF download needed)
DEFAULT_MODELS_TORCHAUDIO: dict[str, str] = {
    "en": "WAV2VEC2_ASR_BASE_960H",
    "fr": "VOXPOPULI_ASR_BASE_10K_FR",
    "de": "VOXPOPULI_ASR_BASE_10K_DE",
    "es": "VOXPOPULI_ASR_BASE_10K_ES",
    "it": "VOXPOPULI_ASR_BASE_10K_IT",
}

# HuggingFace Hub models (downloaded on first use)
DEFAULT_MODELS_HF: dict[str, str] = {
    "ja": "jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
    "zh": "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn",
    "nl": "jonatasgrosman/wav2vec2-large-xlsr-53-dutch",
    "uk": "Yehor/wav2vec2-xls-r-300m-uk-with-small-lm",
    "pt": "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese",
    "ar": "jonatasgrosman/wav2vec2-large-xlsr-53-arabic",
    "cs": "comodoro/wav2vec2-xls-r-300m-cs-250",
    "ru": "jonatasgrosman/wav2vec2-large-xlsr-53-russian",
    "pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish",
    "hu": "jonatasgrosman/wav2vec2-large-xlsr-53-hungarian",
    "fi": "jonatasgrosman/wav2vec2-large-xlsr-53-finnish",
    "fa": "jonatasgrosman/wav2vec2-large-xlsr-53-persian",
    "el": "jonatasgrosman/wav2vec2-large-xlsr-53-greek",
    "tr": "mpoyraz/wav2vec2-xls-r-300m-cv7-turkish",
    "da": "saattrupdan/wav2vec2-xls-r-300m-ftspeech",
    "he": "imvladikon/wav2vec2-xls-r-300m-hebrew",
    "vi": "nguyenvulebinh/wav2vec2-base-vi-vlsp2020",
    "ko": "kresnik/wav2vec2-large-xlsr-korean",
    "ur": "kingabzpro/wav2vec2-large-xls-r-300m-Urdu",
    "te": "anuragshas/wav2vec2-large-xlsr-53-telugu",
    "hi": "theainerd/Wav2Vec2-large-xlsr-hindi",
    "ca": "softcatala/wav2vec2-large-xlsr-catala",
    "ml": "gvs/wav2vec2-large-xlsr-malayalam",
    "no": "NbAiLab/nb-wav2vec2-1b-bokmaal-v2",
    "nn": "NbAiLab/nb-wav2vec2-1b-nynorsk",
    "sk": "comodoro/wav2vec2-xls-r-300m-sk-cv8",
    "sl": "anton-l/wav2vec2-large-xlsr-53-slovenian",
    "hr": "classla/wav2vec2-xls-r-parlaspeech-hr",
    "ro": "gigant/romanian-wav2vec2",
    "eu": "stefan-it/wav2vec2-large-xlsr-53-basque",
    "gl": "ifrz/wav2vec2-large-xlsr-galician",
    "ka": "xsway/wav2vec2-large-xlsr-georgian",
    "lv": "jimregan/wav2vec2-large-xlsr-latvian-cv",
    "tl": "Khalsuu/filipino-wav2vec2-l-xls-r-300m-official",
    "sv": "KBLab/wav2vec2-large-voxrex-swedish",
}


class AlignModelMetadata:
    """Metadata returned alongside a loaded alignment model."""

    __slots__ = ("language", "dictionary", "pipeline_type")

    def __init__(
        self,
        language: str,
        dictionary: dict[str, int],
        pipeline_type: str,
    ) -> None:
        self.language = language
        self.dictionary = dictionary
        self.pipeline_type = pipeline_type


def is_language_supported(language_code: str) -> bool:
    """Check whether a default alignment model exists for the language."""
    return (
        language_code in DEFAULT_MODELS_TORCHAUDIO or language_code in DEFAULT_MODELS_HF
    )


def load_align_model(
    language_code: str,
    device: str,
    model_name: str | None = None,
    model_dir: str | None = None,
) -> tuple[Any, AlignModelMetadata]:
    """Load a wav2vec2 alignment model for the given language.

    Args:
        language_code: ISO 639-1 language code (e.g. ``"en"``).
        device: Torch device string (``"cpu"``, ``"cuda"``, or ``"mps"``).
        model_name: Override the default model for this language.
        model_dir: Local directory for caching model weights.

    Returns:
        Tuple of (model, metadata).

    Raises:
        ValueError: If no model is available for the language.
    """
    if model_name is None:
        if language_code in DEFAULT_MODELS_TORCHAUDIO:
            model_name = DEFAULT_MODELS_TORCHAUDIO[language_code]
        elif language_code in DEFAULT_MODELS_HF:
            model_name = DEFAULT_MODELS_HF[language_code]
        else:
            raise ValueError(
                f"No default alignment model for language '{language_code}'. "
                "Provide a wav2vec2 model name via the model_name parameter."
            )

    # Try torchaudio pipeline first
    if model_name in torchaudio.pipelines.__all__:
        return _load_torchaudio_model(model_name, language_code, device, model_dir)

    # Fall back to HuggingFace
    return _load_hf_model(model_name, language_code, device, model_dir)


def _load_torchaudio_model(
    model_name: str,
    language_code: str,
    device: str,
    model_dir: str | None,
) -> tuple[Any, AlignModelMetadata]:
    """Load a model from torchaudio.pipelines."""
    bundle = torchaudio.pipelines.__dict__[model_name]
    dl_kwargs: dict[str, Any] = {}
    if model_dir is not None:
        dl_kwargs["model_dir"] = model_dir
    model = bundle.get_model(dl_kwargs=dl_kwargs).to(device)
    labels = bundle.get_labels()
    dictionary = {c.lower(): i for i, c in enumerate(labels)}
    metadata = AlignModelMetadata(
        language=language_code,
        dictionary=dictionary,
        pipeline_type="torchaudio",
    )
    logger.info(
        "Loaded torchaudio alignment model %s for language '%s'",
        model_name,
        language_code,
    )
    return model, metadata


def _load_hf_model(
    model_name: str,
    language_code: str,
    device: str,
    model_dir: str | None,
) -> tuple[Any, AlignModelMetadata]:
    """Load a model from HuggingFace Hub."""
    processor = Wav2Vec2Processor.from_pretrained(model_name, cache_dir=model_dir)
    model = Wav2Vec2ForCTC.from_pretrained(model_name, cache_dir=model_dir)
    model = model.to(device)
    dictionary = {
        char.lower(): code for char, code in processor.tokenizer.get_vocab().items()
    }
    metadata = AlignModelMetadata(
        language=language_code,
        dictionary=dictionary,
        pipeline_type="huggingface",
    )
    logger.info(
        "Loaded HuggingFace alignment model %s for language '%s'",
        model_name,
        language_code,
    )
    return model, metadata
