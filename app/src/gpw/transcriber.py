from __future__ import annotations

from pathlib import Path
import logging

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class FasterWhisperTranscriber:
    def __init__(
        self,
        model_size: str = "small.en",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model: WhisperModel | None = None

    def _load_model(self) -> WhisperModel:
        if self._model is None:
            logger.info(
                "Loading faster-whisper model: model=%s device=%s compute_type=%s",
                self.model_size,
                self.device,
                self.compute_type,
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def transcribe(self, audio_path: str | Path, language: str | None = "en", vad_filter: bool = True) -> str:
        model = self._load_model()
        logger.info("Transcribing audio: %s", audio_path)
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=vad_filter,
            beam_size=5,
        )
        text_parts = [segment.text.strip() for segment in segments if segment.text.strip()]
        raw = " ".join(text_parts).strip()
        logger.info("Transcription complete: chars=%s language=%s", len(raw), getattr(info, "language", None))
        return raw
