# ============================================================
# test_unit_heartbeat.py
#
# Unit tests for the heartbeat system (heartbeat.py + config.py).
# No LLM, no network — run immediately.
#
# Run: uv run test_unit_heartbeat.py
# ============================================================

import traceback

# ════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════


def _run(name: str, fn) -> bool:
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        print(f"  ERROR {name}: {e}")
        traceback.print_exc()
        return False


# ════════════════════════════════════════════════════════════
# UNIT TESTS — parse_interval_seconds
# ════════════════════════════════════════════════════════════


def test_parse_minutes():
    from selma.heartbeat import parse_interval_seconds

    assert parse_interval_seconds("30m") == 1800
    assert parse_interval_seconds("1m") == 60


def test_parse_hours():
    from selma.heartbeat import parse_interval_seconds

    assert parse_interval_seconds("1h") == 3600
    assert parse_interval_seconds("2h") == 7200


def test_parse_seconds():
    from selma.heartbeat import parse_interval_seconds

    assert parse_interval_seconds("90s") == 90
    assert parse_interval_seconds("0s") == 0


def test_parse_zero_disables():
    from selma.heartbeat import parse_interval_seconds

    assert parse_interval_seconds("0m") == 0


def test_parse_invalid_returns_zero():
    from selma.heartbeat import parse_interval_seconds

    assert parse_interval_seconds("abc") == 0
    assert parse_interval_seconds("") == 0


# ════════════════════════════════════════════════════════════
# UNIT TESTS — is_within_active_hours
# ════════════════════════════════════════════════════════════


def test_active_hours_always_open():
    from selma.config import ActiveHoursConfig
    from selma.heartbeat import is_within_active_hours

    cfg = ActiveHoursConfig(start="00:00", end="23:59", timezone="UTC")
    assert is_within_active_hours(cfg) is True


def test_active_hours_always_closed():
    """Window with start > end is always closed."""
    from selma.config import ActiveHoursConfig
    from selma.heartbeat import is_within_active_hours

    cfg = ActiveHoursConfig(start="23:59", end="00:00", timezone="UTC")
    assert is_within_active_hours(cfg) is False


def test_active_hours_invalid_timezone_fallback():
    """Invalid timezone → True (run safely)."""
    from selma.config import ActiveHoursConfig
    from selma.heartbeat import is_within_active_hours

    cfg = ActiveHoursConfig(start="00:00", end="23:59", timezone="Mars/Olympus_Mons")
    assert is_within_active_hours(cfg) is True


# ════════════════════════════════════════════════════════════
# UNIT TESTS — is_heartbeat_content_effectively_empty
# ════════════════════════════════════════════════════════════


def test_empty_none_not_empty():
    """Missing file (None) → not empty, run starts anyway."""
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    assert is_heartbeat_content_effectively_empty(None) is False


def test_empty_blank_string():
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    assert is_heartbeat_content_effectively_empty("") is True
    assert is_heartbeat_content_effectively_empty("   \n\n  ") is True


def test_empty_only_headers():
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    assert is_heartbeat_content_effectively_empty("# Heartbeat\n## Tasks\n") is True


def test_empty_only_list_stubs():
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    assert is_heartbeat_content_effectively_empty("- [ ]\n- \n* \n+ \n") is True


def test_empty_only_fences():
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    assert is_heartbeat_content_effectively_empty("```markdown\n```\n") is True


def test_empty_mixed_skippable():
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    content = "# Heartbeat\n\n- [ ]\n\n```\n```\n"
    assert is_heartbeat_content_effectively_empty(content) is True


def test_not_empty_real_content():
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    assert is_heartbeat_content_effectively_empty("- Check emails\n") is False
    assert is_heartbeat_content_effectively_empty("# Title\nCheck email") is False


def test_not_empty_checked_list_item():
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    # "- [x] done" has content → not empty
    assert is_heartbeat_content_effectively_empty("- [x] Done task\n") is False


def test_not_empty_hash_without_space():
    """#TODO without a space is not an ATX header — treated as content."""
    from selma.heartbeat import is_heartbeat_content_effectively_empty

    assert is_heartbeat_content_effectively_empty("#TODO check this") is False


# ════════════════════════════════════════════════════════════
# UNIT TESTS — strip_heartbeat_token
# ════════════════════════════════════════════════════════════


def test_strip_none_is_ack():
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token(None)
    assert r["should_skip"] is True
    assert r["did_strip"] is False


def test_strip_empty_is_ack():
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token("   ")
    assert r["should_skip"] is True


def test_strip_token_only_heartbeat_mode():
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token("HEARTBEAT_OK", mode="heartbeat")
    assert r["should_skip"] is True
    assert r["did_strip"] is True
    assert r["text"] == ""


def test_strip_token_with_trailing_punct():
    from selma.heartbeat import strip_heartbeat_token

    for suffix in [".", "!!!", "---"]:
        r = strip_heartbeat_token(f"HEARTBEAT_OK{suffix}", mode="heartbeat")
        assert r["should_skip"] is True, f"failed for suffix {suffix!r}"


def test_strip_short_padding_is_ack():
    """Token + short padding ≤ ack_max_chars → silent."""
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token("There is nothing to do. HEARTBEAT_OK", mode="heartbeat")
    assert r["should_skip"] is True


def test_strip_long_content_is_alert():
    """Token + long text > 300 characters → deliver."""
    from selma.heartbeat import strip_heartbeat_token

    long = "A" * 301
    r = strip_heartbeat_token(f"{long} HEARTBEAT_OK", mode="heartbeat")
    assert r["should_skip"] is False
    assert r["text"] == long
    assert r["did_strip"] is True


def test_strip_no_token_in_text():
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token("Dringende Aufgabe wartet!", mode="heartbeat")
    assert r["should_skip"] is False
    assert r["did_strip"] is False
    assert r["text"] == "Dringende Aufgabe wartet!"


def test_strip_token_at_start_message_mode():
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token("HEARTBEAT_OK hello", mode="message")
    assert r["should_skip"] is False
    assert r["text"] == "hello"
    assert r["did_strip"] is True


def test_strip_token_at_end_message_mode():
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token("hello HEARTBEAT_OK", mode="message")
    assert r["should_skip"] is False
    assert r["text"] == "hello"
    assert r["did_strip"] is True


def test_strip_token_in_middle_not_stripped():
    """Token in the middle → not handled."""
    from selma.heartbeat import strip_heartbeat_token

    text = "hello HEARTBEAT_OK there"
    r = strip_heartbeat_token(text, mode="message")
    assert r["should_skip"] is False
    assert r["did_strip"] is False
    assert r["text"] == text


def test_strip_html_wrapped_token():
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token("<b>HEARTBEAT_OK</b>", mode="heartbeat")
    assert r["should_skip"] is True


def test_strip_markdown_wrapped_token():
    from selma.heartbeat import strip_heartbeat_token

    r = strip_heartbeat_token("**HEARTBEAT_OK**", mode="heartbeat")
    assert r["should_skip"] is True


def test_strip_custom_max_ack_chars():
    from selma.heartbeat import strip_heartbeat_token

    # With max_ack_chars=5 → "hello" (5 characters) exactly at the limit → silent
    r = strip_heartbeat_token("hello HEARTBEAT_OK", mode="heartbeat", max_ack_chars=5)
    assert r["should_skip"] is True
    # 6 characters → deliver
    r2 = strip_heartbeat_token("hello2 HEARTBEAT_OK", mode="heartbeat", max_ack_chars=5)
    assert r2["should_skip"] is False


# ════════════════════════════════════════════════════════════
# UNIT TESTS — HeartbeatConfig (config.py)
# ════════════════════════════════════════════════════════════


def test_heartbeat_config_defaults():
    from selma.config import HeartbeatConfig

    cfg = HeartbeatConfig()
    assert cfg.every == "0m"
    assert cfg.target == "none"
    assert cfg.light_context is False
    assert cfg.isolated_session is False
    assert cfg.ack_max_chars == 300
    assert cfg.active_hours is None


def test_heartbeat_config_from_dict():
    from selma.config import HeartbeatConfig

    cfg = HeartbeatConfig(
        **{
            "every": "30m",
            "target": "last",
            "light_context": True,
            "isolated_session": True,
            "ack_max_chars": 100,
        }
    )
    assert cfg.every == "30m"
    assert cfg.target == "last"
    assert cfg.light_context is True
    assert cfg.isolated_session is True
    assert cfg.ack_max_chars == 100


def test_heartbeat_config_with_active_hours():
    from selma.config import ActiveHoursConfig, HeartbeatConfig

    cfg = HeartbeatConfig(
        every="1h",
        active_hours=ActiveHoursConfig(start="09:00", end="22:00", timezone="Europe/Berlin"),
    )
    assert cfg.active_hours is not None
    assert cfg.active_hours.start == "09:00"
    assert cfg.active_hours.timezone == "Europe/Berlin"


def test_selma_config_has_heartbeat():
    """HeartbeatConfig is integrated in SelmaConfig."""
    from selma.config import SelmaConfig

    cfg = SelmaConfig()
    assert hasattr(cfg, "heartbeat")
    assert cfg.heartbeat.every == "0m"


# ════════════════════════════════════════════════════════════
# TEST RUNNER
# ════════════════════════════════════════════════════════════

TESTS = [
    # parse_interval_seconds
    test_parse_minutes,
    test_parse_hours,
    test_parse_seconds,
    test_parse_zero_disables,
    test_parse_invalid_returns_zero,
    # is_within_active_hours
    test_active_hours_always_open,
    test_active_hours_always_closed,
    test_active_hours_invalid_timezone_fallback,
    # is_heartbeat_content_effectively_empty
    test_empty_none_not_empty,
    test_empty_blank_string,
    test_empty_only_headers,
    test_empty_only_list_stubs,
    test_empty_only_fences,
    test_empty_mixed_skippable,
    test_not_empty_real_content,
    test_not_empty_checked_list_item,
    test_not_empty_hash_without_space,
    # strip_heartbeat_token
    test_strip_none_is_ack,
    test_strip_empty_is_ack,
    test_strip_token_only_heartbeat_mode,
    test_strip_token_with_trailing_punct,
    test_strip_short_padding_is_ack,
    test_strip_long_content_is_alert,
    test_strip_no_token_in_text,
    test_strip_token_at_start_message_mode,
    test_strip_token_at_end_message_mode,
    test_strip_token_in_middle_not_stripped,
    test_strip_html_wrapped_token,
    test_strip_markdown_wrapped_token,
    test_strip_custom_max_ack_chars,
    # HeartbeatConfig
    test_heartbeat_config_defaults,
    test_heartbeat_config_from_dict,
    test_heartbeat_config_with_active_hours,
    test_selma_config_has_heartbeat,
]


if __name__ == "__main__":
    print()
    print("=" * 54)
    print("  Heartbeat Unit Tests")
    print("=" * 54)

    passed = sum(_run(fn.__name__, fn) for fn in TESTS)
    total = len(TESTS)

    print()
    print("=" * 54)
    result = "✓" if passed == total else "✗"
    print(f"  Result: {passed}/{total} passed  {result}")
    print("=" * 54)
    print()

    if passed < total:
        raise SystemExit(1)
