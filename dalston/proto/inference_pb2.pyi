from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AudioFormat(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PCM_S16LE_16K: _ClassVar[AudioFormat]
    PCM_F32LE_16K: _ClassVar[AudioFormat]
    FILE: _ClassVar[AudioFormat]
PCM_S16LE_16K: AudioFormat
PCM_F32LE_16K: AudioFormat
FILE: AudioFormat

class TranscribeRequest(_message.Message):
    __slots__ = ("audio", "format", "model_id", "config")
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    audio: bytes
    format: AudioFormat
    model_id: str
    config: TranscribeConfig
    def __init__(self, audio: _Optional[bytes] = ..., format: _Optional[_Union[AudioFormat, str]] = ..., model_id: _Optional[str] = ..., config: _Optional[_Union[TranscribeConfig, _Mapping]] = ...) -> None: ...

class TranscribeConfig(_message.Message):
    __slots__ = ("language", "beam_size", "vad_filter", "word_timestamps", "temperature", "task", "initial_prompt", "hotwords")
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    BEAM_SIZE_FIELD_NUMBER: _ClassVar[int]
    VAD_FILTER_FIELD_NUMBER: _ClassVar[int]
    WORD_TIMESTAMPS_FIELD_NUMBER: _ClassVar[int]
    TEMPERATURE_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    INITIAL_PROMPT_FIELD_NUMBER: _ClassVar[int]
    HOTWORDS_FIELD_NUMBER: _ClassVar[int]
    language: str
    beam_size: int
    vad_filter: bool
    word_timestamps: bool
    temperature: float
    task: str
    initial_prompt: str
    hotwords: str
    def __init__(self, language: _Optional[str] = ..., beam_size: _Optional[int] = ..., vad_filter: bool = ..., word_timestamps: bool = ..., temperature: _Optional[float] = ..., task: _Optional[str] = ..., initial_prompt: _Optional[str] = ..., hotwords: _Optional[str] = ...) -> None: ...

class TranscribeResponse(_message.Message):
    __slots__ = ("segments", "language", "language_probability", "duration")
    SEGMENTS_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_PROBABILITY_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    segments: _containers.RepeatedCompositeFieldContainer[Segment]
    language: str
    language_probability: float
    duration: float
    def __init__(self, segments: _Optional[_Iterable[_Union[Segment, _Mapping]]] = ..., language: _Optional[str] = ..., language_probability: _Optional[float] = ..., duration: _Optional[float] = ...) -> None: ...

class Segment(_message.Message):
    __slots__ = ("start", "end", "text", "words", "confidence", "avg_logprob", "compression_ratio", "no_speech_prob")
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    WORDS_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    AVG_LOGPROB_FIELD_NUMBER: _ClassVar[int]
    COMPRESSION_RATIO_FIELD_NUMBER: _ClassVar[int]
    NO_SPEECH_PROB_FIELD_NUMBER: _ClassVar[int]
    start: float
    end: float
    text: str
    words: _containers.RepeatedCompositeFieldContainer[Word]
    confidence: float
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float
    def __init__(self, start: _Optional[float] = ..., end: _Optional[float] = ..., text: _Optional[str] = ..., words: _Optional[_Iterable[_Union[Word, _Mapping]]] = ..., confidence: _Optional[float] = ..., avg_logprob: _Optional[float] = ..., compression_ratio: _Optional[float] = ..., no_speech_prob: _Optional[float] = ...) -> None: ...

class Word(_message.Message):
    __slots__ = ("word", "start", "end", "probability")
    WORD_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    PROBABILITY_FIELD_NUMBER: _ClassVar[int]
    word: str
    start: float
    end: float
    probability: float
    def __init__(self, word: _Optional[str] = ..., start: _Optional[float] = ..., end: _Optional[float] = ..., probability: _Optional[float] = ...) -> None: ...

class StatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class StatusResponse(_message.Message):
    __slots__ = ("runtime", "device", "loaded_models", "total_capacity", "available_capacity", "healthy")
    RUNTIME_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    LOADED_MODELS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_CAPACITY_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_CAPACITY_FIELD_NUMBER: _ClassVar[int]
    HEALTHY_FIELD_NUMBER: _ClassVar[int]
    runtime: str
    device: str
    loaded_models: _containers.RepeatedScalarFieldContainer[str]
    total_capacity: int
    available_capacity: int
    healthy: bool
    def __init__(self, runtime: _Optional[str] = ..., device: _Optional[str] = ..., loaded_models: _Optional[_Iterable[str]] = ..., total_capacity: _Optional[int] = ..., available_capacity: _Optional[int] = ..., healthy: bool = ...) -> None: ...
