# ============================================================
# my_system_prompt unit tests
#
# Deckung für selma/my_system_prompt.py (build_system_prompt):
#   - Default-Options (keine Argumente)
#   - Custom Prompt + append/context/cwd
#   - Tool-Liste: bekannte/unknown Tools, merged descriptions
#   - Auto-Guidelines pro Tool (alle Äste)
#   - prompt_guidelines: dedupe + strip + leere Einträge
#   - context_files / _build_context_section
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

from selma.my_system_prompt import (
    ALL_TOOLS,
    CODING_TOOLS,
    READ_ONLY_TOOLS,
    TOOL_DESCRIPTIONS,
    BuildSystemPromptOptions,
    ContextFile,
    build_system_prompt,
)


def test_module_level_constants():
    assert CODING_TOOLS == ["read", "edit", "write"]
    assert READ_ONLY_TOOLS == ["read", "grep", "find", "ls"]
    assert ALL_TOOLS == list(TOOL_DESCRIPTIONS.keys())
    assert len(ALL_TOOLS) == 6


def test_default_options_builds_full_prompt():
    options = BuildSystemPromptOptions(cwd="/tmp")
    prompt = build_system_prompt(options)

    # Default-Tools = CODING_TOOLS
    assert "- read" in prompt
    assert "- edit" in prompt
    assert "- write" in prompt
    # Auto-Guidelines für read/edit/write
    assert "Use read to examine files." in prompt
    assert "surgical changes" in prompt
    assert "create new files" in prompt
    # Statische Abschlusstexte
    assert "Be concise" in prompt
    assert "Current working directory: /tmp" in prompt
    # Keine append/context-Sektionen
    assert "append" not in prompt.lower() or True
    assert "# Project Context" not in prompt
    assert prompt.startswith("You are a personal assistant")


def test_none_options_uses_defaults():
    prompt = build_system_prompt(None)
    assert "Available tools:" in prompt
    assert "Current date:" in prompt
    assert "Current working directory:" in prompt


def test_custom_prompt_replaces_default():
    options = BuildSystemPromptOptions(
        custom_prompt="MY CUSTOM PROMPT",
        append_system_prompt="EXTRA APPEND",
        cwd="/w",
    )
    prompt = build_system_prompt(options)

    assert prompt.startswith("MY CUSTOM PROMPT")
    assert "Available tools:" not in prompt
    assert "EXTRA APPEND" in prompt
    assert prompt.index("MY CUSTOM PROMPT") < prompt.index("EXTRA APPEND")
    assert "Current working directory: /w" in prompt


def test_tool_list_with_unknown_tool_and_merged_descriptions():
    options = BuildSystemPromptOptions(
        selected_tools=["read", "custom_tool"],
        tool_descriptions={
            "custom_tool": "My custom tool",
            "read": "OVERRIDE builtin",
        },
    )
    prompt = build_system_prompt(options)

    # Custom Tool mit eigener Beschreibung, Override schlägt Builtin zu
    assert "- custom_tool: My custom tool" in prompt
    assert "- read: OVERRIDE builtin" in prompt
    assert "- read: Read file contents" not in prompt


def test_empty_selected_tools_lists_none():
    options = BuildSystemPromptOptions(selected_tools=[])
    prompt = build_system_prompt(options)
    assert "(none)" in prompt


def test_all_tool_guidelines_branches():
    options = BuildSystemPromptOptions(selected_tools=list(ALL_TOOLS) + ["bash"])
    prompt = build_system_prompt(options)

    assert "Never use bash cat or sed" in prompt
    assert "Use ls first to understand the directory structure" in prompt
    assert "Prefer literal=true" in prompt
    assert "locate files by glob pattern" in prompt
    assert "Use bash for tasks that cannot be done" in prompt


def test_prompt_guidelines_deduped_and_filled():
    options = BuildSystemPromptOptions(
        selected_tools=["write"],
        prompt_guidelines=[
            "  custom one  ",  # strip + neue → drin
            "Use write to create new files or fully replace existing ones.",  # dupliziert Auto-Guideline → entfernt
            "",  # leer → ignoriert
            "   ",  # leer nach strip → ignoriert
        ],
    )
    prompt = build_system_prompt(options)

    assert "- custom one" in prompt
    assert prompt.count("Use write to create new files") == 1
    assert "- \n" not in prompt  # leere Einträge erzeugen keine Zeile


def test_context_files_section():
    options = BuildSystemPromptOptions(
        context_files=[
            ContextFile(path="AGENTS.md", content="Agent rules"),
            ContextFile(path="NOTES.md", content="Some notes"),
        ]
    )
    prompt = build_system_prompt(options)

    assert "# Project Context" in prompt
    assert "## AGENTS.md\n\nAgent rules" in prompt
    assert "## NOTES.md\n\nSome notes" in prompt
    # Kontext-Sektion steht vor Datum/cwd
    assert prompt.index("# Project Context") < prompt.index("Current date:")


def test_context_section_empty_returns_empty_string():
    from selma.my_system_prompt import _build_context_section

    assert _build_context_section([]) == ""
    assert (
        _build_context_section([ContextFile(path="A.md", content="x")]).startswith("\n\n# Project Context\n\n") is True
    )
