# ============================================================
# audio_tool.py — lokale Audio-Tools (TTS + STT)
#
#   tts_speak       Text → WAV-Datei via piper-tts (ONNX, lokal, offline).
#                   Voice-Pinning: feste Stimme aus der Konfiguration
#                   (Default de_DE-thorsten-medium) — reproduzierbar,
#                   kein Cloud, kein Zufall.
#   stt_transcribe  Audio-Datei → Text via faster-whisper (CTranslate2,
#                   CPU-First, int8). Modellgroeß konfigurierbar, Default
#                   "small" (wie in der Referenz ~/workspace/STT).
#
# Design (2026-10-04, Rolf: "nicht nur Smoke Tests, sondern Unit-Tests"):
#   - Alle Logik liegt in reinen Modul-Funktionen (synthesize_text,
#     transcribe_file, _segments_to_text, ...).
#   - Die Tool-Closures (make_tts_tool / make_stt_tool) sind dunn:
#     Argumente entgegennehmen, Ergebnis als JSON zurueckgeben.
#   - Damit patchen die Unit-Tests exakt die Library-Grenzen
#     (piper.voice.PiperVoice, piper.download_voices.download_voice,
#      faster_whisper.WhisperModel) — deterministisch, ohne Netz,
#     ohne GPU, ohne echte Synthese/Transkription.
#
# Konfiguration (SelmaConfig.audio):
#   audio.tts.voice   — Piper-Voice-Name (Pinned, Default de_DE-thorsten-medium)
#   audio.tts.scale   — Duration-Skala (1.0 = normal, reproduzierbar)
#   audio.stt.model   — faster-whisper-Groesse (Default "small")
#   audio.stt.language— ISO-Code (Default "de"), None = Auto-Detect
#   audio.stt.beam_size — Decodierungs-Beam (Default 1)
# ============================================================

from __future__ import annotations

import json
import os
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selma.agent import AgentTool
    from selma.config import SelmaConfig

# Prozess-interne Caches (Selma läuft ein-threadig).
_STT_MODEL_CACHE: dict[str, Any] = {}


def _probe_audio_engines() -> None:
    """Fail-CLOSED-Check über die optionalen Engines an der Library-Grenze.

    Piper / faster-whisper sind optionale Dependencies (extra ``audio``);
    ihre Importe in diesem Modul sind lazy, damit ``import selma.audio_tool``
    auch ohne Engines funktioniert. Die FACTORY (``make_audio_tools``) ruft
    diesen Check an: schlägt er fehl, werfen die Audio-Tools einen sauberen
    ImportError, statt dass erst der ERSTE TOOL-CALL beim Modell-Laden auf den
    fehlenden Backend prallt.
    """
    failures: list[str] = []
    piper_voice: Any = None
    try:
        from piper.voice import PiperVoice  # type: ignore[import]

        piper_voice = PiperVoice
    except ImportError as e:
        failures.append(f"piper.voice.PiperVoice nicht verfügbar ({e})")

    whisper_model: Any = None
    try:
        from faster_whisper import WhisperModel  # type: ignore[import]

        whisper_model = WhisperModel
    except ImportError as e:
        failures.append(f"faster_whisper.WhisperModel nicht verfügbar ({e})")

    if piper_voice is not None and not isinstance(piper_voice, type):
        failures.append("piper.voice.PiperVoice ist keine Klasse")
    if whisper_model is not None and not isinstance(whisper_model, type):
        failures.append("faster_whisper.WhisperModel ist keine Klasse")
    if failures:
        raise ImportError("TTS/STT-Engines nicht verfügbar: " + "; ".join(failures))


# ── Caches / Pfade ───────────────────────────────────────────


def _cache_root() -> Path:
    d = Path(os.environ.get("SELMA_AUDIO_CACHE_DIR", str(Path.home() / ".cache" / "selma-audio")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _voice_files(voice: str) -> tuple[Path, Path]:
    """Erwartete Dateinamen für eine Piper-Voice (Voice-Pinning!)."""
    cd = _cache_root()
    return cd / f"{voice}.onnx", cd / f"{voice}.onnx.json"


def _tts_voice_path(config: SelmaConfig) -> Path:
    """Pfad der Voice-ONNX-Datei (lädt bei Bedarf per piper)."""
    voice = config.audio.tts.voice
    model, model_json = _voice_files(voice)
    if model.exists() and model_json.exists():
        return model

    from piper.download_voices import download_voice  # type: ignore[import]

    try:
        # Tatsächliche Signatur: download_voice(voice, download_dir=Path, force_redownload=False) -> None
        download_voice(voice, download_dir=model.parent)
    except Exception as e:
        raise RuntimeError(f"Voice-Download fehlgeschlagen ('{voice}'): {e}") from e

    if model.exists() and model_json.exists():
        return model
    candidates = sorted(_cache_root().rglob(f"{voice}.onnx"))
    if not candidates:
        raise RuntimeError(f"Voice-Modell '{voice}' wurde nicht heruntergeladen ({model} fehlt).")
    return candidates[0]


def _load_tts_voice(config: SelmaConfig):
    from piper.voice import PiperVoice  # type: ignore[import]

    return PiperVoice.load(str(_tts_voice_path(config)), include_alignments=False)


def _reset_stt_cache() -> None:
    """Test-Helfer: Cached STT-Modelle leeren."""
    _STT_MODEL_CACHE.clear()


def _stt_model(config: SelmaConfig):
    size = config.audio.stt.model
    if size in _STT_MODEL_CACHE:
        return _STT_MODEL_CACHE[size]
    import faster_whisper  # type: ignore[import]

    model = faster_whisper.WhisperModel(
        size,
        device="cpu",
        compute_type="int8",
        download_root=str(_cache_root() / "whisper"),
    )
    _STT_MODEL_CACHE[size] = model
    return model


# ── Helpers (rein / testbar) ─────────────────────────────────


def _segments_to_text(segments: Any) -> tuple[str, float]:
    """Extrahiert (text, last_end) aus faster-whisper-Segmenten (oder dicts)."""
    parts: list[str] = []
    last_end = 0.0
    for s in segments:
        txt = getattr(s, "text", None) if not isinstance(s, dict) else s.get("text")
        if txt:
            parts.append(str(txt).strip())
        end = getattr(s, "end", None) if not isinstance(s, dict) else s.get("end")
        if end and float(end) > last_end:
            last_end = float(end)
    return " ".join(p for p in parts if p).strip(), last_end


# ── Kern-Logik ───────────────────────────────────────────────


def synthesize_text(
    config: SelmaConfig,
    text: str,
    output_path: str,
    *,
    scale: float | None = None,
) -> dict[str, Any]:
    """Text → WAV-Datei via piper. Gibt {"text","path","bytes"} zurück."""
    body = (text or "").strip()
    if not body:
        raise ValueError("TTS: Text darf nicht leer sein.")

    out = Path(output_path)
    if str(out.parent) not in ("", "."):
        out.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Voice-Pinning: die aus der Konfiguration stehende feste Stimme
        # wird geladen (voice-Parameter überschreibt nur für Einzelausnahmen).
        piper_voice = _load_tts_voice(config)

        scale_f = float(scale if scale is not None else config.audio.tts.scale)
        try:
            from piper.config import SynthesisConfig  # type: ignore[import]

            syn = SynthesisConfig(length_scale=max(0.1, scale_f))
        except Exception:
            syn = None

        with wave.open(str(out), "wb") as wav_file:
            piper_voice.synthesize_wav(body, wav_file, syn_config=syn)
        size = out.stat().st_size
    except FileNotFoundError as e:
        raise RuntimeError(
            f"TTS: Voice-Modell fehlt — Stimme '{config.audio.tts.voice}' zuerst herunterladen. Ursprung: {e}"
        ) from e
    except ValueError:
        raise
    except Exception as e:
        raise RuntimeError(f"TTS-Synthese fehlgeschlagen: {e}") from e

    if size <= 0:
        raise RuntimeError("TTS: Leere Audio-Datei erzeugt (0 Bytes).")
    # 0-Byte-Check allein reicht NICHT: eine Datei mit nur RIFF-Header
    # (44 Bytes, 0 Audio-Frames) ist technisch gültiges WAV, aber stumm.
    try:
        with wave.open(str(out), "rb") as chk:
            frames = chk.getnframes()
    except (wave.Error, EOFError) as e:
        raise RuntimeError(f"TTS: Unlesbares WAV-Ergebnis: {out} ({e})") from e
    if frames <= 0:
        raise RuntimeError("TTS: Leere Audio-Datei erzeugt (0 Audio-Frames).")
    return {"text": body, "path": str(out), "bytes": int(size)}


def transcribe_file(
    config: SelmaConfig,
    audio_path: str,
    *,
    language: str | None = None,
    beam_size: int | None = None,
) -> dict[str, Any]:
    """Audio-Datei → Text via faster-whisper. Gibt {"text","duration","language"} zurück."""
    ap = Path(audio_path)
    if not ap.exists():
        raise ValueError(f"STT: Audio-Datei existiert nicht: {ap}")

    lang = language if language is not None else config.audio.stt.language
    bs = max(1, min(int(beam_size if beam_size is not None else config.audio.stt.beam_size), 5))

    try:
        model = _stt_model(config)
        segments, info = model.transcribe(
            str(ap),
            beam_size=bs,
            language=lang,
            vad_filter=True,
        )
        segments = list(segments)
        text, last_end = _segments_to_text(segments)
    except ValueError:
        raise
    except Exception as e:
        raise RuntimeError(f"STT-Transkription fehlgeschlagen: {e}") from e

    lang_out = getattr(info, "language", None) or lang
    return {"text": text, "duration": round(last_end, 2), "language": str(lang_out)}


def _default_config() -> SelmaConfig:
    from selma.config import SelmaConfig as _SC

    return _SC()


# ── Tool-Wrapper (für create_selma_tools) ────────────────────


def make_tts_tool(config: SelmaConfig) -> AgentTool:
    from selma.agent import AgentTool, ToolSchema

    # Fail-CLOSED pro Aufruf (keine Cache): ohne Engine → ImportError.
    _probe_audio_engines()

    def execute(
        text: str,
        output_path: str = "tts_wav_out/wav",
        scale: float | None = None,
        **_: Any,
    ) -> str:
        result = synthesize_text(config, text, output_path, scale=scale)
        return json.dumps(result, ensure_ascii=False)

    return AgentTool(
        name="tts_speak",
        description=(
            "Text in eine WAV-Datei übersetzen (lokal, piper-tts, offline, reproduzierbar). "
            "Feste, konfigurierte Stimme (Default de_DE-thorsten-medium). "
            "Ergebnis: JSON mit text/path/bytes."
        ),
        parameters=ToolSchema(
            properties={
                "text": {"type": "string", "description": "Text, der gesprochen werden soll"},
                "output_path": {
                    "type": "string",
                    "description": "Ziel-Pfad der WAV-Datei (Zwischenverzeichnisse werden angelegt)",
                },
                "scale": {
                    "type": "number",
                    "description": "Optionale Duration-Skala (1.0 = normal)",
                },
            },
            required=["text"],
        ),
        execute=execute,
    )


def make_stt_tool(config: SelmaConfig) -> AgentTool:
    from selma.agent import AgentTool, ToolSchema

    def execute(
        audio_path: str,
        language: str | None = None,
        beam_size: int | None = None,
        **_: Any,
    ) -> str:
        result = transcribe_file(config, audio_path, language=language, beam_size=beam_size)
        return json.dumps(result, ensure_ascii=False)

    return AgentTool(
        name="stt_transcribe",
        description=(
            "Audio-Datei in Text transkribieren (lokal, faster-whisper, CPU, int8). "
            "Default-Modell 'small', Sprache Deutsch. Ergebnis: JSON mit text/duration/language."
        ),
        parameters=ToolSchema(
            properties={
                "audio_path": {"type": "string", "description": "Pfad zur Audio-Datei"},
                "language": {
                    "type": "string",
                    "description": "ISO-Sprache (Default aus Konfiguration)",
                },
                "beam_size": {
                    "type": "integer",
                    "description": "Decodierungs-Beam (Default 1)",
                },
            },
            required=["audio_path"],
        ),
        execute=execute,
    )


def make_audio_tools(config: SelmaConfig | None = None) -> list[AgentTool]:
    """Zwei Audio-Tools; Config wird falls nötig aus der Umgebung gelesen."""
    cfg = config or _default_config()
    return [make_tts_tool(cfg), make_stt_tool(cfg)]
