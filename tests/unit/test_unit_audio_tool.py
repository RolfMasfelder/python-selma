# ============================================================
# audio_tool.py unit tests
#
# Deckung für selma/audio_tool.py (tts_speak / stt_transcribe).
# Hermetisch: piper + faster_whisper werden an den Library-Grenzen
# gemocked (sys.modules-Fakes) — es wird KEIN echtes Audio gerendert
# und VERSTANDEN. (Echte Engine-Qualität wurde per Smoke-Run am
# 2026-10-04 validiert; API-Doku: .selma/workspace/AUDIO_API.md.)
#
# Alle Fake-Signaturen folgen den REALEN APIs (per inspect.signature
# am venv verifiziert, in AUDIO_API.md festgehalten):
#   - PiperVoice.load(model_path, config_path=None, ...) -> Voice
#     Voice.synthesize_wav(text, wav_file, syn_config=..., ...) -> None
#   - download_voice(voice, download_dir=Path, force_redownload=False) -> None   (PLAINE FUNCTION!)
#   - SynthesisConfig(length_scale=...)
#   - WhisperModel(size, device=, compute_type=, download_root=, ...)
#     .transcribe(audio, beam_size=, language=, vad_filter=, ...)
#       -> (Iterable[Segment], TranscriptionInfo)
#
# Konventionen:
#   - KEIN pytest-asyncio (sync-Tests), große Timeouts.
#   - Isolation: SELMA_STATE_DIR + SELMA_AUDIO_CACHE_DIR (tmp_path).
#   - Fakes sind PLAIN FUNCTIONS/CLASSES ohne Instanz-Typen-Konflikte:
#     download_voice ist im echten piper eine Funktion (keine Klasse!).
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

import selma.audio_tool as audio_tool
from selma.config import SelmaConfig

if TYPE_CHECKING:
    from collections.abc import Iterator

# ── Isolation (function-scope autouse) ────────────────────────


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """SELMA_STATE_DIR + SELMA_AUDIO_CACHE_DIR + <root>/.selma absichern.

    Der Audio-Cache wird extra isoliert, damit _tts_voice_path nie in
    ~/.cache/... (Produktions-Cache) liest und nie echt lädt.
    """
    monkeypatch.setenv("SELMA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SELMA_AUDIO_CACHE_DIR", str(tmp_path / "audiocache"))
    (tmp_path / ".selma").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_fake_state() -> Iterator[None]:
    """Klassen-mutable Fake-Aufruf-Listen vor/nach JEDEM Test leeren."""
    yield
    FakePiperVoice.load_calls.clear()
    FakePiperVoice.synth_calls.clear()
    BrokenSynthVoice.load_calls.clear()
    FakeWhisperModel.inits.clear()
    FakeWhisperModel.transcribe_calls.clear()
    FakeWhisperModel._error = None
    FakeWhisperModel._segments = [("hallo", 1.2), ("welt.", 3.5)]
    audio_tool._STT_MODEL_CACHE.clear()


VOICE_NAME = "de_DE-thorsten-medium"


def _cache_root() -> Path:
    """Audio-Cache-Root aus der isolierten Env (wird via autouse gesetzt)."""
    import os

    return Path(os.environ["SELMA_AUDIO_CACHE_DIR"])


def _seed_voice() -> Path:
    """Audio-Cache mit Voice-Datei-Struktur bestücken (KEIN echter Download)."""
    cd = _cache_root()
    cd.mkdir(parents=True, exist_ok=True)
    (cd / f"{VOICE_NAME}.onnx").write_bytes(b"onnx-fake")
    (cd / f"{VOICE_NAME}.onnx.json").write_text("{}")
    return cd / f"{VOICE_NAME}.onnx"


def _break_import(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    """sys.modules[module] = None → `import <module>` wirft ImportError."""
    monkeypatch.setitem(sys.modules, module, None)


# ── Fakes (reale Signaturen, s. AUDIO_API.md) ─────────────────


class FakePiperVoice:
    """Double von piper.PiperVoice (load / synthesize_wav), reale Signatur."""

    load_calls: list[dict[str, Any]] = []
    synth_calls: list[tuple[str, Any]] = []

    @classmethod
    def load(cls, model_path: Any, config_path: Any = None, **kw: Any) -> FakePiperVoice:
        cls.load_calls.append({"model_path": str(model_path), **kw})
        return cls(str(model_path))

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def synthesize_wav(
        self,
        text: str,
        wav_file: Any,
        syn_config: Any = None,
        set_wav_format: bool = True,
        include_alignments: bool = False,
    ) -> None:
        """Schreibt eine minimale, gültige WAV in die (offene) Wave_write.

        Spiegelt die echte piper-API: synthesize_wav() bekommt das ALLEIN
        von uns offene Wave_write und setzt Format + Frames darauf
        (set_wav_format=True ist piper-Default).
        """
        if set_wav_format:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
        wav_file.writeframes((text.replace("\n", " ")).encode("utf-8").ljust(160, b"\x00"))
        type(self).synth_calls.append((text, syn_config))


def _realistic_download_voice() -> Any:
    """PLAINE FUNCTION im Stil des echten piper.download_voices.download_voice.

    Schreibt beide Voice-Dateien nach <download_dir> und liefert None.
    Aufruf-Logik: function.calls listet alle Calls (Voice, Download-DIR).
    """
    calls: list[dict[str, Any]] = []

    def download_voice(voice: str, download_dir: Any, force_redownload: bool = False, **kw: Any) -> None:
        calls.append({"voice": voice, "download_dir": str(download_dir)})
        d = Path(str(download_dir))
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{voice}.onnx").write_bytes(b"onnx-fake")
        (d / f"{voice}.onnx.json").write_text("{}")

    download_voice.calls = calls
    download_voice._dl = calls
    return download_voice


def _broken_download_voice() -> Any:
    """PLAINE FUNCTION: wirft OSError (keine Netz)."""

    def download_voice(voice: str, download_dir: Any, **kw: Any) -> None:
        raise OSError("no-network")

    return download_voice


def _noop_download_voice() -> Any:
    """PLAINE FUNCTION: liefert None ohne eine einzige Datei zu schreiben."""
    calls: list[dict[str, Any]] = []

    def download_voice(voice: str, download_dir: Any, **kw: Any) -> None:
        calls.append({"voice": voice})

    download_voice.calls = calls
    return download_voice


class BrokenSynthVoice:
    """PiperVoice-Double, dessen synthesize_wav einen Fehler wirft."""

    load_calls: list[Any] = []
    synth_calls: list[Any] = []

    @classmethod
    def load(cls, model_path: Any, config_path: Any = None, **kw: Any) -> BrokenSynthVoice:
        cls.load_calls.append(model_path)
        return cls(str(model_path))

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def synthesize_wav(self, _text: str, wav_file: Any, *_a: Any, **kw: Any) -> None:
        # Format ZUERST setzen (wie echtes piper: set_wav_format vor dem
        # Schreiben) — sonst maskiert wave.close() den echten Fehler durch
        # "# channels not specified".
        if kw.get("set_wav_format", True):
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
        raise OSError("espeak-ng-data fehlt")


class SilentSynthVoice:
    """PiperVoice-Double: setzt Format, schreibt ABER keine Frames.

    Ergebnis: 44-Byte-RIFF-Header, 0 Audio-Frames → audio_tool.py
    erkennt das als "Leere Audio-Datei" (getnframes() == 0).
    """

    load_calls: list[Any] = []
    synth_calls: list[Any] = []

    @classmethod
    def load(cls, model_path: Any, config_path: Any = None, **kw: Any) -> SilentSynthVoice:
        cls.load_calls.append(model_path)
        return cls(str(model_path))

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def synthesize_wav(
        self, text: str, wav_file: Any, syn_config: Any = None, set_wav_format: bool = True, **kw: Any
    ) -> None:
        if set_wav_format:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
        type(self).synth_calls.append((text, syn_config))


def _install_piper_fakes(
    monkeypatch: pytest.MonkeyPatch,
    dl: Any | None = None,
    voice_cls: Any = FakePiperVoice,
) -> Any:
    """piper-Module als Fakes in sys.modules setzen (lazy-Imports in audio_tool).

    Liefert die verwendete download_voice-Funktion zurück (zur Aufruf-Logik).
    """
    if dl is None:
        dl = _realistic_download_voice()
    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=voice_cls))
    monkeypatch.setitem(sys.modules, "piper.voice", SimpleNamespace(PiperVoice=voice_cls))
    monkeypatch.setitem(sys.modules, "piper.download_voices", SimpleNamespace(download_voice=dl))
    monkeypatch.setitem(
        sys.modules,
        "piper.config",
        SimpleNamespace(SynthesisConfig=lambda length_scale=1.0, **kw: SimpleNamespace(length_scale=length_scale)),
    )
    return dl


class FakeWhisperModel:
    """Double von faster_whisper.WhisperModel (reale Signatur).

    WhisperModel(size, device=, compute_type=, download_root=, ...)
    — `size` ist PLATZ 1 (Positional), danach Optionen.
    """

    inits: list[dict[str, Any]] = []
    transcribe_calls: list[dict[str, Any]] = []
    _error: Exception | None = None
    _segments: list[tuple[str, float]] = [("hallo", 1.2), ("welt.", 3.5)]
    _info_language: str | None = "de"

    def __init__(self, model_size_or_path: Any, *a: Any, **kw: Any) -> None:
        type(self).inits.append({"size": model_size_or_path, **kw})

    def transcribe(self, audio: Any, **kw: Any) -> tuple[Iterator[Any], Any]:
        type(self).transcribe_calls.append({"audio": audio, **kw})
        if type(self)._error is not None:
            raise type(self)._error
        segments = [SimpleNamespace(start=text[:2], end=end, text=text) for text, end in type(self)._segments]
        return iter(segments), SimpleNamespace(language=type(self)._info_language)


def _install_whisper_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))


def _last_transcribe_kw() -> dict[str, Any]:
    calls: list[dict[str, Any]] = FakeWhisperModel.transcribe_calls
    assert calls, "transcribe() wurde nicht aufgerufen"
    return calls[-1]


# ════════════════════════════════════════════════════════════
# Voice-Resolution (Voice-Pinning)
# ════════════════════════════════════════════════════════════
class TestVoiceResolution:
    def test_voice_files_naming(self) -> None:
        model, model_json = audio_tool._voice_files(VOICE_NAME)
        assert model.name == f"{VOICE_NAME}.onnx"
        assert model_json.name == f"{VOICE_NAME}.onnx.json"

    def test_existing_voice_is_used_without_download(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seed = _seed_voice()
        dl = _install_piper_fakes(monkeypatch)
        assert audio_tool._tts_voice_path(SelmaConfig()) == seed
        assert dl.calls == []  # KEIN Download

    def test_default_voice_is_configured(self) -> None:
        assert SelmaConfig().audio.tts.voice == VOICE_NAME  # Voice-Pinning-Default

    def test_missing_voice_triggers_download(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dl = _install_piper_fakes(monkeypatch)
        path = audio_tool._tts_voice_path(SelmaConfig())
        assert path.exists()
        assert dl.calls[0]["voice"] == VOICE_NAME

    def test_download_failure_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_piper_fakes(monkeypatch, dl=_broken_download_voice())
        with pytest.raises(RuntimeError, match="Voice-Download fehlgeschlagen"):
            audio_tool._tts_voice_path(SelmaConfig())
            # ↑ "Voice-Download fehlgeschlagen ('de_DE-thorsten-medium'): no-network"

    def test_download_noop_without_files_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_piper_fakes(monkeypatch, dl=_noop_download_voice())
        with pytest.raises(RuntimeError, match="nicht heruntergeladen"):
            audio_tool._tts_voice_path(SelmaConfig())


# ════════════════════════════════════════════════════════════
# _segments_to_text (reinigung)
# ════════════════════════════════════════════════════════════
class TestSegmentsToText:
    def test_attribute_segments(self) -> None:
        segs = [SimpleNamespace(text="hallo ", end=1.0), SimpleNamespace(text="welt.", end=2.5)]
        text, last = audio_tool._segments_to_text(segs)
        assert text == "hallo welt."
        assert last == 2.5

    def test_dict_segments(self) -> None:
        segs = [{"text": "a", "end": 0.5}, {"text": "b", "end": 1.7}]
        text, last = audio_tool._segments_to_text(segs)
        assert text == "a b"
        assert last == 1.7

    def test_gaps_and_string_ends(self) -> None:
        segs = [SimpleNamespace(text="  ", end=None), SimpleNamespace(text="x", end="2.3")]
        text, last = audio_tool._segments_to_text(segs)
        assert text == "x"
        assert last == 2.3

    def test_no_segments(self) -> None:
        text, last = audio_tool._segments_to_text([])
        assert text == ""
        assert last == 0.0

    def test_missing_text_attr_skipped(self) -> None:
        segs = [SimpleNamespace(end=1.0), SimpleNamespace(text="ok", end=2.0)]
        text, last = audio_tool._segments_to_text(segs)
        assert text == "ok"
        assert last == 2.0


# ════════════════════════════════════════════════════════════
# synthesize_text (TTS-Kern)
# ════════════════════════════════════════════════════════════
class TestSynthesizeText:
    def test_success_returns_dict_and_valid_wav(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        voice = _seed_voice()
        _install_piper_fakes(monkeypatch)
        out = tmp_path / "out" / "hello.wav"
        result = audio_tool.synthesize_text(SelmaConfig(), "Hallo Welt", str(out))
        assert result["text"] == "Hallo Welt"
        assert result["path"] == str(out)
        assert result["bytes"] > 0
        # Tatsächlich gültige WAV?
        with wave.open(str(out), "rb") as w:
            assert w.getnframes() > 0
        # Voice-Pinning: Engine wurde auf die konfigurierte Voice geladen
        assert FakePiperVoice.load_calls[0]["model_path"] == str(voice)

    @pytest.mark.parametrize("bad", ["", "   \n "])
    def test_empty_text_raises_valueerror(self, bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_piper_fakes(monkeypatch)
        with pytest.raises(ValueError, match="nicht leer"):
            audio_tool.synthesize_text(SelmaConfig(), bad, "irgendwo/x.wav")

    def test_scale_forwarded_to_synthesis_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_voice()
        _install_piper_fakes(monkeypatch)
        audio_tool.synthesize_text(SelmaConfig(), "Text", str(tmp_path / "o.wav"), scale=2.0)
        assert FakePiperVoice.synth_calls[0][1].length_scale == 2.0

    def test_default_scale_from_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_voice()
        _install_piper_fakes(monkeypatch)
        cfg = SelmaConfig()
        cfg.audio.tts.scale = 1.5
        audio_tool.synthesize_text(cfg, "Text", str(tmp_path / "o.wav"))
        assert FakePiperVoice.synth_calls[0][1].length_scale == 1.5

    def test_engine_error_wrapped_in_runtimeerror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_voice()
        _install_piper_fakes(monkeypatch, voice_cls=BrokenSynthVoice)
        with pytest.raises(RuntimeError) as ei:
            audio_tool.synthesize_text(SelmaConfig(), "Text", str(tmp_path / "o.wav"))
        assert "TTS-Synthese fehlgeschlagen" in str(ei.value)
        assert "espeak-ng-data fehlt" in str(ei.value)

    def test_zero_byte_output_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Format gesetzt, aber 0 Audio-Frames → 'Leere Audio-Datei' (getnframes==0)."""
        _seed_voice()
        _install_piper_fakes(monkeypatch, voice_cls=SilentSynthVoice)
        with pytest.raises(RuntimeError, match="Leere Audio-Datei"):
            audio_tool.synthesize_text(SelmaConfig(), "Text", str(tmp_path / "o.wav"))


# ════════════════════════════════════════════════════════════
# make_tts_tool (Tool-Wrapper)
# ════════════════════════════════════════════════════════════
class TestMakeTtsTool:
    def test_schema(self) -> None:
        tool = audio_tool.make_tts_tool(SelmaConfig())
        assert tool.name == "tts_speak"
        assert tool.parameters.required == ["text"]
        assert set(tool.parameters.properties) == {"text", "output_path", "scale"}
        assert "TTS" in tool.description or "tts" in tool.description

    def test_execute_returns_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_voice()
        _install_piper_fakes(monkeypatch)
        tool = audio_tool.make_tts_tool(SelmaConfig())
        result = tool.execute(text="Test", output_path=str(tmp_path / "x.wav"))
        payload = json.loads(result)
        assert payload["text"] == "Test"
        assert Path(payload["path"]).exists()
        assert payload["bytes"] > 0

    def test_execute_error_payload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_voice()
        _install_piper_fakes(monkeypatch, voice_cls=BrokenSynthVoice)
        tool = audio_tool.make_tts_tool(SelmaConfig())
        with pytest.raises(RuntimeError):
            tool.execute(text="t", output_path=str(tmp_path / "x.wav"))


# ════════════════════════════════════════════════════════════
# _stt_model (Caching + cpu/int8-Pinning)
# ════════════════════════════════════════════════════════════
class TestSttModel:
    def test_pinned_to_cpu_int8_and_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_whisper_fakes(monkeypatch)
        m1 = audio_tool._stt_model(SelmaConfig())
        m2 = audio_tool._stt_model(SelmaConfig())
        assert m1 is m2  # Cache-Treffer
        init = FakeWhisperModel.inits[0]
        assert init["size"] == "small"
        assert init["device"] == "cpu"
        assert init["compute_type"] == "int8"

    def test_model_size_from_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_whisper_fakes(monkeypatch)
        cfg = SelmaConfig()
        cfg.audio.stt.model = "base"
        audio_tool._stt_model(cfg)
        assert FakeWhisperModel.inits[-1]["size"] == "base"

    def test_cache_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_whisper_fakes(monkeypatch)
        audio_tool._stt_model(SelmaConfig())
        before = len(FakeWhisperModel.inits)
        audio_tool._reset_stt_cache()
        audio_tool._stt_model(SelmaConfig())
        assert len(FakeWhisperModel.inits) == before + 1

    def test_missing_dependency_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _break_import(monkeypatch, "faster_whisper")
        with pytest.raises(ImportError):
            audio_tool._stt_model(SelmaConfig())


# ════════════════════════════════════════════════════════════
# transcribe_file (STT-Kern)
# ════════════════════════════════════════════════════════════
class TestTranscribeFile:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        _install_whisper_fakes(monkeypatch)
        self.audio: Path = tmp_path / "a.wav"
        self.audio.write_bytes(b"FAKEWAVBYTES")
        self.cfg: SelmaConfig = SelmaConfig()
        FakeWhisperModel._error = None
        FakeWhisperModel._segments = [("hallo", 1.2), ("welt.", 3.5)]
        FakeWhisperModel._info_language = "de"

    def test_success_returns_dict(self) -> None:
        result = audio_tool.transcribe_file(self.cfg, str(self.audio))
        assert result["text"] == "hallo welt."
        assert result["duration"] == 3.5
        assert result["language"] == "de"

    def test_defaults_from_config(self) -> None:
        self.cfg.audio.stt.language = "en"
        self.cfg.audio.stt.beam_size = 5
        audio_tool.transcribe_file(self.cfg, str(self.audio))
        last = _last_transcribe_kw()
        assert last["language"] == "en"
        assert last["beam_size"] == 5
        assert last["vad_filter"] is True

    def test_explicit_overrides(self) -> None:
        audio_tool.transcribe_file(self.cfg, str(self.audio), language="fr", beam_size=4)
        last = _last_transcribe_kw()
        assert last["language"] == "fr"
        assert last["beam_size"] == 4

    @pytest.mark.parametrize("beam,expected", [(99, 5), (0, 1), (-3, 1), (2, 2)])
    def test_beam_size_clamped(self, beam: Any, expected: int) -> None:
        audio_tool.transcribe_file(self.cfg, str(self.audio), beam_size=beam)
        assert _last_transcribe_kw()["beam_size"] == expected

    def test_missing_file_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="existiert nicht"):
            audio_tool.transcribe_file(self.cfg, str(self.audio.parent / "nix.wav"))

    def test_engine_error_wrapped(self) -> None:
        FakeWhisperModel._error = RuntimeError("CTranslate2-panic")
        with pytest.raises(RuntimeError) as ei:
            audio_tool.transcribe_file(self.cfg, str(self.audio))
        assert "STT-Transkription fehlgeschlagen" in str(ei.value)
        assert "CTranslate2-panic" in str(ei.value)

    def test_engine_valueerror_not_wrapped(self) -> None:
        FakeWhisperModel._error = ValueError("bad input")
        with pytest.raises(ValueError, match="bad input"):
            audio_tool.transcribe_file(self.cfg, str(self.audio))

    def test_empty_segments_give_empty_text(self) -> None:
        FakeWhisperModel._segments = []
        result = audio_tool.transcribe_file(self.cfg, str(self.audio))
        assert result["text"] == ""
        assert result["duration"] == 0.0

    def test_info_language_none_falls_back_to_config(self) -> None:
        FakeWhisperModel._info_language = None
        result = audio_tool.transcribe_file(self.cfg, str(self.audio), language="de")
        assert result["language"] == "de"


# ════════════════════════════════════════════════════════════
# make_stt_tool (Tool-Wrapper)
# ════════════════════════════════════════════════════════════
class TestMakeSttTool:
    def test_schema(self) -> None:
        tool = audio_tool.make_stt_tool(SelmaConfig())
        assert tool.name == "stt_transcribe"
        assert tool.parameters.required == ["audio_path"]
        assert set(tool.parameters.properties) == {"audio_path", "language", "beam_size"}

    def test_execute_returns_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_whisper_fakes(monkeypatch)
        a = tmp_path / "a.wav"
        a.write_bytes(b"FAKE")
        tool = audio_tool.make_stt_tool(SelmaConfig())
        payload = json.loads(tool.execute(audio_path=str(a)))
        assert payload["text"] == "hallo welt."
        assert payload["duration"] == 3.5
        assert payload["language"] == "de"


# ════════════════════════════════════════════════════════════
# make_audio_tools (Factories + Defaults + Probe)
# ════════════════════════════════════════════════════════════
class TestMakeAudioTools:
    def test_returns_both_tools_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_piper_fakes(monkeypatch)
        _install_whisper_fakes(monkeypatch)
        tools = audio_tool.make_audio_tools(SelmaConfig())
        assert [t.name for t in tools] == ["tts_speak", "stt_transcribe"]

    def test_default_config_when_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_voice()
        _install_piper_fakes(monkeypatch)
        _install_whisper_fakes(monkeypatch)
        tools = audio_tool.make_audio_tools()  # None → SelmaConfig()
        tools[0].execute(text="t", output_path=str(tmp_path / "d.wav"))
        assert FakePiperVoice.load_calls
        assert VOICE_NAME in FakePiperVoice.load_calls[0]["model_path"]

    def test_probe_raises_when_engines_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-CLOSED: ohne Engines wirft die Factory (ImportError)."""
        _break_import(monkeypatch, "piper.voice")
        _break_import(monkeypatch, "faster_whisper")
        with pytest.raises(ImportError, match="nicht verfügbar"):
            audio_tool.make_audio_tools(SelmaConfig())


# ════════════════════════════════════════════════════════════
# tools.py-Integration (Audio-Tools in create_selma_tools)
# ════════════════════════════════════════════════════════════
class TestCreateSelmaToolsIntegration:
    def test_audio_tools_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import selma.tools as tools

        _seed_voice()
        _install_piper_fakes(monkeypatch)
        _install_whisper_fakes(monkeypatch)
        (tmp_path / ".selma" / "agents").mkdir(parents=True, exist_ok=True)
        names = [t.name for t in tools.create_selma_tools(str(tmp_path), SelmaConfig())]
        assert "tts_speak" in names
        assert "stt_transcribe" in names

    def test_audio_tools_import_error_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Wenn piper/faster-whisper fehlen: tools.py fängt ImportError →
        alle anderen Tools kommen trotzdem (keine import-Crash, keine
        halbe Factory mit kaputtem Backend)."""
        import selma.tools as tools

        _break_import(monkeypatch, "piper")
        _break_import(monkeypatch, "piper.voice")
        _break_import(monkeypatch, "piper.config")
        _break_import(monkeypatch, "piper.download_voices")
        _break_import(monkeypatch, "faster_whisper")
        (tmp_path / ".selma" / "agents").mkdir(parents=True, exist_ok=True)
        names = [t.name for t in tools.create_selma_tools(str(tmp_path), SelmaConfig())]
        assert "read" in names and "write" in names
        assert "tts_speak" not in names  # Engine fehlt → Audio-Tools sauber weg
        assert "stt_transcribe" not in names
