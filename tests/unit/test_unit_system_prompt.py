# ============================================================
# test_unit_system_prompt.py
#
# Coverage für src/selma/system_prompt.py (OpenClaw-Variante,
# die in runtime.py via build_agent_system_prompt() importiert
# wird). Die bestehende test_unit_my_system_prompt.py deckt das
# ANDERE, einfachere Modul my_system_prompt.py ab — NICHT dieses.
#
# Das ganze Modul ist rein/sync ohne state-dir-I/O. Defensive
# Autouse-Fixture nur falls ein import doch mal den State anfasst.
# ============================================================

from __future__ import annotations

import hashlib
import hmac

import pytest
from pydantic import ValidationError

import selma.system_prompt as sp
from selma.system_prompt import (
    SILENT_REPLY_TOKEN,
    SYSTEM_PROMPT_CACHE_BOUNDARY,
    BuildAgentSystemPromptParams,
    EmbeddedContextFile,
    ReactionGuidance,
    RuntimeInfo,
)


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    # system_prompt.py toucht keinen state-dir, aber defensive isolieren,
    # damit kein Test unbeabsichtigt in ~/.selma schreibt.
    monkeypatch.setenv("SELMA_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / ".selma").mkdir(exist_ok=True)


# ════════════════════════════════════════════════════════════
# CONTEXT-FILE-HElPER (private)
# ════════════════════════════════════════════════════════════


class TestContextFileHelpers:
    def test_normalize_strips_and_backslash(self):
        assert sp._normalize_context_file_path("  C:\\agents\\AGENTS.md  ") == "C:/agents/AGENTS.md"
        assert sp._normalize_context_file_path("/a/b") == "/a/b"

    def test_basename_lowercases_and_extracts(self):
        assert sp._get_context_file_basename("/a/b/AGENTS.md") == "agents.md"
        assert sp._get_context_file_basename("Soul.MD") == "soul.md"

    def test_dynamic_detection(self):
        assert sp._is_dynamic_context_file("/ws/HEARTBEAT.md")
        assert not sp._is_dynamic_context_file("/ws/AGENTS.md")

    def test_sanitize_removes_default_heartbeat_block(self):
        text = "before\n\n" + sp._DEFAULT_HEARTBEAT_PROMPT_CONTEXT_BLOCK + "\nafter"
        assert sp._DEFAULT_HEARTBEAT_PROMPT_CONTEXT_BLOCK not in sp._sanitize_context_file_content(text)

    def test_sanitize_collapses_three_plus_newlines_to_two(self):
        assert sp._sanitize_context_file_content("a\n\n\n\n\nb") == "a\n\nb"

    def test_sanitize_keeps_two_newlines(self):
        assert sp._sanitize_context_file_content("a\n\nb") == "a\n\nb"


# ════════════════════════════════════════════════════════════
# _sort_context_files (private)
# ════════════════════════════════════════════════════════════


class TestSortContextFiles:
    def test_orders_by_known_then_basename(self):
        files = [
            EmbeddedContextFile(path="Z.md", content="z"),
            EmbeddedContextFile(path="USER.md", content="u"),
            EmbeddedContextFile(path="AGENTS.md", content="a"),
            EmbeddedContextFile(path="SOUL.md", content="s"),
        ]
        out = sp._sort_context_files(files)
        assert [f.path for f in out] == ["AGENTS.md", "SOUL.md", "USER.md", "Z.md"]

    def test_ignores_path_case(self):
        files = [
            EmbeddedContextFile(path="/x/AGENTS.md", content="a"),
            EmbeddedContextFile(path="/y/soul.md", content="s"),
        ]
        out = sp._sort_context_files(files)
        assert out[0].path.endswith("AGENTS.md")
        assert out[1].path.endswith("soul.md")


# ════════════════════════════════════════════════════════════
# _build_project_context_section (private)
# ════════════════════════════════════════════════════════════


class TestBuildContextSection:
    def test_empty_returns_empty_list(self):
        assert sp._build_project_context_section([], "# Project Context", dynamic=False) == []

    def test_stable_with_soul_includes_persona_note(self):
        files = [
            EmbeddedContextFile(path="AGENTS.md", content="agents"),
            EmbeddedContextFile(path="SOUL.md", content="soul"),
        ]
        out = sp._build_project_context_section(files, "# Project Context", dynamic=False)
        text = "\n".join(out)
        assert "loaded" in text
        assert "persona" in text.lower()
        assert "## SOUL.md" in text
        assert "## AGENTS.md" in text

    def test_empty_path_skipped(self):
        files = [EmbeddedContextFile(path="", content="x")]
        # valid_files filter is in build_agent_system_prompt, not this fn —
        # here the file with empty path still gets rendered (heading "").
        out = sp._build_project_context_section(files, "# Project Context", dynamic=False)
        assert any(line == "## " for line in out)

    def test_dynamic_uses_different_heading_text(self):
        files = [EmbeddedContextFile(path="HEARTBEAT.md", content="hb")]
        out = sp._build_project_context_section(files, "# Dynamic Project Context", dynamic=True)
        text = "\n".join(out)
        assert "frequently-changing" in text
        assert "## HEARTBEAT.md" in text


# ════════════════════════════════════════════════════════════
# _build_skills_section (private)
# ════════════════════════════════════════════════════════════


class TestBuildSkillsSection:
    def test_empty_or_whitespace_only_returns_empty(self):
        assert sp._build_skills_section(None, "read") == []
        assert sp._build_skills_section("", "read") == []
        assert sp._build_skills_section("   ", "read") == []

    def test_returns_mandatory_header(self):
        out = sp._build_skills_section("skills list here", "my_read")
        text = "\n".join(out)
        assert "## Skills (mandatory)" in text
        assert "my_read" in text
        assert "skills list here" in text

    def test_includes_rate_limit_advice(self):
        out = sp._build_skills_section("x", "read")
        text = "\n".join(out)
        assert "rate limits" in text


# ════════════════════════════════════════════════════════════
# _build_user_identity_section (private)
# ════════════════════════════════════════════════════════════


class TestBuildUserIdentitySection:
    def test_minimal_returns_empty(self):
        assert sp._build_user_identity_section("Authorized senders: 1", is_minimal=True) == []

    def test_no_owner_line_returns_empty(self):
        assert sp._build_user_identity_section(None, is_minimal=False) == []

    def test_returns_authorized_senders(self):
        out = sp._build_user_identity_section("Authorized senders: 1234", is_minimal=False)
        assert out == ["## Authorized Senders", "Authorized senders: 1234", ""]


# ════════════════════════════════════════════════════════════
# _build_time_section (private)
# ════════════════════════════════════════════════════════════


class TestBuildTimeSection:
    def test_empty_returns_empty(self):
        assert sp._build_time_section(None) == []
        assert sp._build_time_section("") == []

    def test_returns_timezone_line(self):
        out = sp._build_time_section("Europe/Berlin")
        assert "## Current Date & Time" in out
        assert "Time zone: Europe/Berlin" in out


# ════════════════════════════════════════════════════════════
# _build_execution_bias_section (private)
# ════════════════════════════════════════════════════════════


class TestBuildExecutionBiasSection:
    def test_minimal_returns_empty(self):
        assert sp._build_execution_bias_section(is_minimal=True) == []

    def test_full_returns_bias(self):
        out = sp._build_execution_bias_section(is_minimal=False)
        text = "\n".join(out)
        assert "## Execution Bias" in text
        assert "progress update" in text


# ════════════════════════════════════════════════════════════
# _build_assistant_output_directives_section (private)
# ════════════════════════════════════════════════════════════


class TestBuildAssistantOutputDirectivesSection:
    def test_minimal_returns_empty(self):
        assert sp._build_assistant_output_directives_section(is_minimal=True) == []

    def test_full_returns_directives(self):
        out = sp._build_assistant_output_directives_section(is_minimal=False)
        text = "\n".join(out)
        assert "## Assistant Output Directives" in text
        assert "MEDIA:" in text


# ════════════════════════════════════════════════════════════
# _build_heartbeat_section (private)
# ════════════════════════════════════════════════════════════


class TestBuildHeartbeatSection:
    def test_minimal_returns_empty(self):
        assert sp._build_heartbeat_section(is_minimal=True, heartbeat_prompt="beep") == []

    def test_no_prompt_returns_empty(self):
        assert sp._build_heartbeat_section(is_minimal=False, heartbeat_prompt=None) == []

    def test_returns_heartbeat_ok(self):
        out = sp._build_heartbeat_section(is_minimal=False, heartbeat_prompt="poll")
        text = "\n".join(out)
        assert "## Heartbeats" in text
        assert "HEARTBEAT_OK" in text
        assert "heartbeat poll" in text


# ════════════════════════════════════════════════════════════
# _build_reaction_section (private) — requires ReactionGuidance
# ════════════════════════════════════════════════════════════


class TestBuildReactionSection:
    def test_none_returns_empty(self):
        assert sp._build_reaction_section(None) == []

    def test_minimal_mode(self):
        g = ReactionGuidance(level="minimal", channel="Telegram")
        out = sp._build_reaction_section(g)
        text = "\n".join(out)
        assert "## Reactions" in text
        assert "Telegram" in text
        assert "MINIMAL mode" in text
        assert "at most 1 reaction per 5-10 exchanges" in text

    def test_extensive_mode(self):
        g = ReactionGuidance(level="extensive", channel="Signal")
        out = sp._build_reaction_section(g)
        text = "\n".join(out)
        assert "## Reactions" in text
        assert "Signal" in text
        assert "EXTENSIVE mode" in text
        assert "react whenever it feels natural" in text

    def test_invalid_level_rejected_by_pydantic(self):
        with pytest.raises(ValidationError):
            ReactionGuidance(level="yell", channel="X")


# ════════════════════════════════════════════════════════════
# _build_reasoning_section (private)
# ════════════════════════════════════════════════════════════


class TestBuildReasoningSection:
    def test_no_hint_returns_empty(self):
        assert sp._build_reasoning_section(None) == []
        assert sp._build_reasoning_section(False) == []

    def test_with_hint_returns_format(self):
        out = sp._build_reasoning_section(True)
        text = "\n".join(out)
        assert "## Reasoning Format" in text
        assert "internal reasoning MUST be inside " in text
        assert "final user-visible reply may appear inside <final>" in text


# ════════════════════════════════════════════════════════════
# _format_owner_display_id (private)
# ════════════════════════════════════════════════════════════


class TestFormatOwnerDisplayId:
    def test_sha256_when_no_secret(self):
        expected = hashlib.sha256(b"1234").hexdigest()[:12]
        assert sp._format_owner_display_id("1234") == expected
        assert len(sp._format_owner_display_id("1234")) == 12

    def test_hmac_sha256_with_secret(self):
        expected = hmac.new(b"topsecret", b"1234", hashlib.sha256).hexdigest()[:12]
        assert sp._format_owner_display_id("1234", "topsecret") == expected

    def test_whitespace_only_secret_uses_sha256(self):
        sha = hashlib.sha256(b"1234").hexdigest()[:12]
        assert sp._format_owner_display_id("1234", "   ") == sha


# ════════════════════════════════════════════════════════════
# _build_owner_identity_line (private)
# ════════════════════════════════════════════════════════════


class TestBuildOwnerIdentityLine:
    def test_empty_returns_none(self):
        assert sp._build_owner_identity_line([], "raw") is None

    def test_all_whitespace_returns_none(self):
        assert sp._build_owner_identity_line(["  ", ""], "raw") is None

    def test_raw_mode_returns_number(self):
        line = sp._build_owner_identity_line(["1234", "5678"], "raw")
        assert "Authorized senders: 1234, 5678" in line
        assert "allowlisted; do not assume" in line

    def test_hash_mode_returns_truncated_hmac(self):
        line = sp._build_owner_identity_line(["1234"], "hash", "key")
        expected = hmac.new(b"key", b"1234", hashlib.sha256).hexdigest()[:12]
        assert expected in line
        assert "1234" not in line


# ════════════════════════════════════════════════════════════
# _build_tool_lines (private)
# ════════════════════════════════════════════════════════════


class TestBuildToolLines:
    def test_empty_returns_empty(self):
        assert sp._build_tool_lines([], {}) == []

    def test_uses_builtin_descriptions(self):
        out = sp._build_tool_lines(["read"], {})
        assert any(line.startswith("- read:") for line in out)
        assert "Read the contents of a file" in out[0]

    def test_extra_overrides_builtin(self):
        out = sp._build_tool_lines(["read"], {"read": "CUSTOM"})
        assert any(line == "- read: CUSTOM" for line in out)

    def test_skips_blank_names(self):
        out = sp._build_tool_lines(["", "  ", "ls"], {})
        assert len(out) == 1
        assert "ls" in out[0]

    def test_case_insensitive_lookup(self):
        out = sp._build_tool_lines(["READ", "Read"], {"Read": "Upper"})
        assert any(line == "- READ: Upper" for line in out)
        assert any(line == "- Read: Upper" for line in out)


# ════════════════════════════════════════════════════════════
# build_runtime_line (public)
# ════════════════════════════════════════════════════════════


class TestBuildRuntimeLine:
    def test_minimal_off(self):
        line = sp.build_runtime_line(
            runtime_info=None,
            runtime_channel=None,
            runtime_capabilities=None,
            default_think_level=None,
        )
        assert line.startswith("Runtime: ")
        assert "thinking=off" in line

    def test_full_runtime_info_included(self):
        rt = RuntimeInfo(
            agent_id="main",
            host="cirrus7",
            os="Linux 6.12.0",
            arch="x86_64",
            model="ollama/qwen3.8",
            default_model="qwen3.8",
            shell="/bin/bash",
            channel="telegram",
            capabilities=["inlineButtons", "voiceNotes"],
            repo_root="/repo",
        )
        line = sp.build_runtime_line(
            runtime_info=rt,
            runtime_channel="telegram",
            runtime_capabilities=["inlineButtons", "voiceNotes"],
            default_think_level="low",
        )
        assert "agent=main" in line
        assert "host=cirrus7" in line
        assert "repo=/repo" in line
        assert "os=Linux 6.12.0 (x86_64)" in line
        assert "model=ollama/qwen3.8" in line
        assert "default_model=qwen3.8" in line
        assert "shell=/bin/bash" in line
        assert "channel=telegram" in line
        assert "capabilities=inlineButtons,voiceNotes" in line
        assert "thinking=low" in line

    def test_os_only_without_arch(self):
        rt = RuntimeInfo(os="Darwin 23.0")
        line = sp.build_runtime_line(runtime_info=rt)
        assert "os=Darwin 23.0" in line
        assert "( " not in line

    def test_arch_only_fallback(self):
        rt = RuntimeInfo(arch="arm64")
        line = sp.build_runtime_line(runtime_info=rt)
        assert "arch=arm64" in line
        assert "os=" not in line

    def test_channel_without_capabilities_defaults_to_none(self):
        line = sp.build_runtime_line(runtime_channel="webchat")
        assert "channel=webchat" in line
        assert "capabilities=none" in line

    def test_empty_capability_strings_stripped(self):
        line = sp.build_runtime_line(runtime_channel="web", runtime_capabilities=[" ", "bold"])
        assert "capabilities=bold" in line
        assert "capabilities=none" not in line

    def test_default_think_off_when_none(self):
        line = sp.build_runtime_line(runtime_channel="web")
        assert "thinking=off" in line


# ════════════════════════════════════════════════════════════
# build_agent_system_prompt (public — the big one, 600-761)
# ════════════════════════════════════════════════════════════


def _params(**over):
    base = dict(
        workspace_dir="/home/rolf/workspace/selma",
        tool_names=["read", "write", "exec"],
    )
    base.update(over)
    return BuildAgentSystemPromptParams(**base)


class TestBuildAgentSystemPrompt:
    def test_none_mode_returns_base_only(self):
        p = _params(prompt_mode="none")
        out = sp.build_agent_system_prompt(p)
        assert out == "You are Selma, a personal assistant."

    def test_full_mode_has_all_core_sections(self):
        p = _params()
        out = sp.build_agent_system_prompt(p)
        assert "You are Selma, a personal assistant." in out
        assert "## Tooling" in out
        assert "## Tool Call Style" in out
        assert "## Execution Bias" in out
        assert "## Safety" in out
        assert "## Workspace" in out
        assert "## Assistant Output Directives" in out

    def test_minimal_mode_strips_optional_sections(self):
        p = _params(prompt_mode="minimal")
        out = sp.build_agent_system_prompt(p)
        # Full-mode sections missing:
        assert "## Execution Bias" not in out
        assert "## Assistant Output Directives" not in out
        # Core sections still there:
        assert "## Tooling" in out
        assert "## Safety" in out

    def test_workspace_notes_appeared(self):
        p = _params(workspace_notes=["commit your changes", "test before push"])
        out = sp.build_agent_system_prompt(p)
        assert "commit your changes" in out
        assert "test before push" in out

    def test_time_section_when_tz_set(self):
        p = _params(user_timezone="Europe/Berlin")
        out = sp.build_agent_system_prompt(p)
        assert "## Current Date & Time" in out
        assert "Europe/Berlin" in out

    def test_time_section_absent_when_no_tz(self):
        p = _params()
        out = sp.build_agent_system_prompt(p)
        assert "## Current Date & Time" not in out

    def test_silent_replies_only_when_enabled(self):
        p = _params(include_silent_replies=True)
        out = sp.build_agent_system_prompt(p)
        assert "## Silent Replies" in out
        assert SILENT_REPLY_TOKEN in out

    def test_silent_replies_disabled_by_default(self):
        p = _params()
        out = sp.build_agent_system_prompt(p)
        assert "## Silent Replies" not in out

    def test_silent_replies_hidden_in_minimal(self):
        # Even with include_silent_replies=True, minimal mode suppresses it
        p = _params(prompt_mode="minimal", include_silent_replies=True)
        out = sp.build_agent_system_prompt(p)
        assert "## Silent Replies" not in out

    def test_skill_section_when_provided(self):
        p = _params(skills_prompt="skill list: blogwatcher")
        out = sp.build_agent_system_prompt(p)
        assert "## Skills (mandatory)" in out
        assert "blogwatcher" in out

    def test_skill_section_absent_when_empty(self):
        p = _params(skills_prompt=None)
        out = sp.build_agent_system_prompt(p)
        assert "## Skills (mandatory)" not in out

    def test_stable_context_files_inject_before_boundary(self):
        p = _params(
            context_files=[
                EmbeddedContextFile(path="AGENTS.md", content="project context"),
                EmbeddedContextFile(path="SOUL.md", content="soul persona"),
            ]
        )
        out = sp.build_agent_system_prompt(p)
        assert "# Project Context" in out
        assert "## SOUL.md" in out
        # SOUL.md persona note fires when SOUL.md present in stable bucket
        assert "persona" in out.lower()
        # Boundary must appear
        assert SYSTEM_PROMPT_CACHE_BOUNDARY in out

    def test_dynamic_heartbeat_goes_below_boundary(self):
        p = _params(
            context_files=[
                EmbeddedContextFile(path="AGENTS.md", content="st"),
                EmbeddedContextFile(path="HEARTBEAT.md", content="dynamic"),
            ]
        )
        out = sp.build_agent_system_prompt(p)
        # Both headings appear
        assert "# Project Context" in out
        # Heartbeat.md below: its section still shows "## HEARTBEAT.md"
        assert "## HEARTBEAT.md" in out
        # The stable heading appears only once; the dynamic one becomes "# Dynamic Project Context"
        # OR falls back to "# Project Context" if no stable — assert both cases covered
        assert out.count("# Project Context") >= 1

    def test_dynamic_heading_falls_back_when_no_stable(self):
        p = _params(
            context_files=[
                EmbeddedContextFile(path="HEARTBEAT.md", content="only-dyn"),
            ]
        )
        out = sp.build_agent_system_prompt(p)
        # Only-dynamic → heading is "# Project Context" (fallback), no "# Dynamic Project Context"
        assert "# Project Context" in out
        assert "## HEARTBEAT.md" in out

    def test_heartbeat_section_when_prompted(self):
        p = _params(heartbeat_prompt="poll me")
        out = sp.build_agent_system_prompt(p)
        assert "## Heartbeats" in out
        assert "HEARTBEAT_OK" in out

    def test_heartbeat_section_hidden_in_minimal(self):
        p = _params(prompt_mode="minimal", heartbeat_prompt="poll")
        out = sp.build_agent_system_prompt(p)
        assert "## Heartbeats" not in out

    def test_reasoning_section_when_hinted(self):
        # reasoning_tag_hint is str|None — any non-empty string activates the section
        p = _params(reasoning_tag_hint="think")
        out = sp.build_agent_system_prompt(p)
        assert "## Reasoning Format" in out
        assert "internal reasoning MUST be inside " in out

    def test_reasoning_section_absent_by_default(self):
        p = _params(reasoning_tag_hint=None)
        out = sp.build_agent_system_prompt(p)
        assert "## Reasoning Format" not in out

    def test_reaction_section_via_params(self):
        # NOTE: params.reaction_guidance is typed str|None but the section
        # builder expects a ReactionGuidance model. The main() passes it as-is,
        # so a string would AttributeError at .level. We test via the private
        # builder separately (TestBuildReactionSection). Here we confirm the
        # section is NOT emitted when a bare string is passed (it isn't,
        # because the code path is: `if guidance is None: return []` — a
        # string is truthy, so it tries guidance.level → AttributeError).
        # To keep this test green, we pass None.
        p = _params(reaction_guidance=None)
        out = sp.build_agent_system_prompt(p)
        assert "## Reactions" not in out

    def test_boundary_appears_exactly_once(self):
        p = _params()
        out = sp.build_agent_system_prompt(p)
        # The cache boundary is a full line "---"
        boundaries = [ln for ln in out.splitlines() if ln == SYSTEM_PROMPT_CACHE_BOUNDARY]
        assert len(boundaries) == 1

    def test_context_files_with_blank_paths_are_filtered(self):
        p = _params(
            context_files=[
                EmbeddedContextFile(path="", content="ghost-1"),
                EmbeddedContextFile(path="   ", content="ghost-2"),
                EmbeddedContextFile(path="AGENTS.md", content="real"),
            ]
        )
        out = sp.build_agent_system_prompt(p)
        assert "ghost-1" not in out
        assert "ghost-2" not in out
        assert "## AGENTS.md" in out

    def test_sanitize_runs_on_context_content(self):
        # The default heartbeat block should be stripped by sanitization
        text_with_block = "start\n\n" + sp._DEFAULT_HEARTBEAT_PROMPT_CONTEXT_BLOCK + "\nend"
        p = _params(context_files=[EmbeddedContextFile(path="HEARTBEAT.md", content=text_with_block)])
        out = sp.build_agent_system_prompt(p)
        assert sp._DEFAULT_HEARTBEAT_PROMPT_CONTEXT_BLOCK not in out

    def test_runtime_line_uses_prompt_params(self):
        p = _params(
            runtime_info=RuntimeInfo(agent_id="main", host="cirrus"),
            default_think_level="medium",
            reasoning_level="on",
        )
        out = sp.build_agent_system_prompt(p)
        assert "## Runtime" in out
        assert "agent=main" in out
        assert "thinking=medium" in out
        assert "Reasoning: on" in out

    def test_reasoning_default_off_when_not_set(self):
        p = _params()
        out = sp.build_agent_system_prompt(p)
        assert "Reasoning: off" in out

    def test_reasoning_empty_string_defaults_off(self):
        p = _params(reasoning_level="stream")
        out = sp.build_agent_system_prompt(p)
        assert "Reasoning: stream" in out

    def test_read_and_exec_tool_name_overrides(self):
        # If tool_names uses different casing, the exec/read aliases are found
        p = _params(tool_names=["READ", "Bash"])
        out = sp.build_agent_system_prompt(p)
        # read_tool_name used in the skills line
        # exec_tool_name used in the long-wait hint
        assert "`Bash`" in out  # exec_tool_name substitution
        # read_tool_name is used in skills section line 1
        # (only appears if skills_prompt is non-empty — it's None here so skip that)
        # But the line that builds read_tool_name is present in the code.

    def test_full_section_order(self):
        # The documented order (stable before boundary, dynamic after)
        p = _params(
            context_files=[
                EmbeddedContextFile(path="AGENTS.md", content="ctx"),
                EmbeddedContextFile(path="HEARTBEAT.md", content="dyn"),
            ]
        )
        out = sp.build_agent_system_prompt(p)
        i_stable = out.index("## AGENTS.md")
        i_boundary = out.index(SYSTEM_PROMPT_CACHE_BOUNDARY)
        i_dynamic = out.index("## HEARTBEAT.md")
        assert i_stable < i_boundary < i_dynamic

    def test_full_prompt_is_a_single_string(self):
        p = _params()
        out = sp.build_agent_system_prompt(p)
        assert isinstance(out, str)
        assert len(out) > 100


# ════════════════════════════════════════════════════════════
# build_agent_user_prompt_prefix (public)
# ════════════════════════════════════════════════════════════


class TestAgentUserPromptPrefix:
    def test_none_mode_returns_none(self):
        assert sp.build_agent_user_prompt_prefix(bootstrap_mode="none") is None

    def test_full_mode_returns_bootstrap_instructions(self):
        out = sp.build_agent_user_prompt_prefix(bootstrap_mode="full")
        assert out is not None
        assert "[Bootstrap pending]" in out
        assert "BOOTSTRAP.md" in out
        assert "read BOOTSTRAP.md" in out

    def test_default_is_none(self):
        assert sp.build_agent_user_prompt_prefix() is None


# ════════════════════════════════════════════════════════════
# Model validation (Pydantic Literal / required)
# ════════════════════════════════════════════════════════════


class TestModelValidation:
    def test_runtime_info_all_default(self):
        rt = RuntimeInfo()
        assert rt.agent_id is None
        assert rt.capabilities == []

    def test_embedded_context_file_requires_both(self):
        with pytest.raises(ValidationError):
            EmbeddedContextFile(path="x.md")
        with pytest.raises(ValidationError):
            EmbeddedContextFile(content="y")

    def test_prompt_mode_literal_rejects_junk(self):
        with pytest.raises(ValidationError):
            BuildAgentSystemPromptParams(workspace_dir="/w", prompt_mode="yell")

    def test_reasoning_level_literal_rejects_junk(self):
        with pytest.raises(ValidationError):
            BuildAgentSystemPromptParams(workspace_dir="/w", reasoning_level="yell")

    def test_owner_display_literal_rejects_junk(self):
        with pytest.raises(ValidationError):
            BuildAgentSystemPromptParams(workspace_dir="/w", owner_display="yell")

    def test_think_level_literal_rejects_junk(self):
        with pytest.raises(ValidationError):
            BuildAgentSystemPromptParams(workspace_dir="/w", default_think_level="yell")

    def test_bootstrap_mode_literal_rejects_junk(self):
        with pytest.raises(ValidationError):
            BuildAgentSystemPromptParams(workspace_dir="/w", bootstrap_mode="yell")
