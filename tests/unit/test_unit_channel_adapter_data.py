# ============================================================
# test_unit_channel_adapter_data.py
#
# Unit tests für selma/channel_adapter.py (Protocol) und selma/data.py.
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import typing

from selma import channel_adapter, data

# ── data.py ──────────────────────────────────────────────


def test_webchatin_defaults_and_values():
    msg = data.WebChatIn(user_id="u1", text="hallo")
    assert msg.user_id == "u1"
    assert msg.text == "hallo"
    assert msg.user_name == "Web User"

    msg2 = data.WebChatIn(user_id="u2", text="hi", user_name="Rolf")
    assert msg2.user_name == "Rolf"


def test_normalized_turn_input_defaults_all_none():
    nti = data.NormalizedTurnInput()
    assert nti.id is None
    assert nti.timestamp is None
    assert nti.body is None
    assert nti.body_for_agent is None
    assert nti.body_for_commands is None
    assert nti.raw is None
    assert nti.session_key is None


def test_normalized_turn_input_full_and_dump():
    nti = data.NormalizedTurnInput(
        id="t1",
        timestamp=123,
        body="raw text",
        body_for_agent="agent text",
        body_for_commands="cmd",
        raw={"any": [1, 2]},
        session_key="web:u1",
    )
    assert nti.id == "t1"
    assert nti.session_key == "web:u1"
    dumped = nti.model_dump(exclude_none=True, exclude={"raw"})
    assert "raw" not in dumped
    assert dumped["body"] == "raw text"


def test_normalized_turn_input_pretty_print(capsys):
    nti = data.NormalizedTurnInput(id="t1", body="test")
    data.NormalizedTurnInput.pretty_print(nti)
    out = capsys.readouterr().out
    assert "NEW MESSAGE RECEIVED" in out
    assert '"id": "t1"' in out
    # pretty_print blendet 'raw' explizit aus
    assert '"raw"' not in out


# ── channel_adapter.py ───────────────────────────────────


def test_protocol_is_runtime_checkable():
    # @runtime_checkable setzt _is_runtime_protocol auf der Protokollklasse
    assert getattr(channel_adapter.ChannelAdapter, "_is_runtime_protocol", False) is True


def _make_fake_adapter_instance():
    """Duck-typed Klasse, die alle Protocol-Mitglieder (Signatur) erfüllt."""

    class FakeAdapter:
        name = "fake"

        @classmethod
        def normalize(cls, raw):
            return data.NormalizedTurnInput()

        @classmethod
        def deliver(cls, context):
            return None

        def is_enabled(self, config):
            return True

        async def start(self, config):
            return None

    return FakeAdapter()


def test_fake_adapter_satisfies_protocol():
    # runtime_checkable prüft nur Vorhandensein + Signatur der Methoden
    # (Rückgabetypen werden nicht validiert)
    assert isinstance(_make_fake_adapter_instance(), channel_adapter.ChannelAdapter)


def test_incomplete_protocol_violation():
    """Fehlende Methode (is_enabled) muss bei isinstance prüfen scheitern."""

    class Incomplete:
        name = "x"

        @classmethod
        def normalize(cls, raw): ...

        @classmethod
        def deliver(cls, context): ...

        async def start(self, config): ...

    assert not isinstance(Incomplete(), channel_adapter.ChannelAdapter)


def test_protocol_annotations_present():
    """Die dokumentierte Signatur existiert als Annotation/Member."""
    for member in ("normalize", "deliver", "is_enabled", "start"):
        assert hasattr(channel_adapter.ChannelAdapter, member), member
    # 'name' ist nur eine Annotation ohne Default → kein klassenattribut
    assert "name" in typing.get_type_hints(channel_adapter.ChannelAdapter) or "name" in getattr(
        channel_adapter.ChannelAdapter, "__annotations__", {}
    )
