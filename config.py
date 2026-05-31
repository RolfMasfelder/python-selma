import json
import time
from pathlib import Path
from typing import Optional, Any, Dict, List, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator
from helper import resolve_state_dir

# Canonical thinking-level constants — import from here, not redefined elsewhere.
THINKING_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})
THINKING_LEVEL_ORDER: list[str] = ["high", "medium", "low"]


# -- Channel sub-models ----------------------------

class TelegramChannelConfig(BaseModel):
    """
    Configuration for the Telegram channel.
    Token is not stored here — comes from TELEGRAM_TOKEN in .env.
    """
    enabled: bool = False


class WebChatChannelConfig(BaseModel):
    """Configuration for the WebChat channel (Uvicorn/FastAPI)."""
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "warning"


class ChannelsConfig(BaseModel):
    """All channel settings bundled together."""
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    webchat: WebChatChannelConfig = Field(default_factory=WebChatChannelConfig)


# -- Model sub-model -------------------------------

class ModelConfig(BaseModel):
    model: str = ""
    timeout_seconds: int = 60
    thinking: str = "low"
    ollama_base_url: str = "http://localhost:11434/v1"


# -- Session sub-models ----------------------------

class SessionResetConfig(BaseModel):
    at_hour: int = 4                    # Daily reset hour (local time)
    idle_minutes: int | None = None     # None = disabled


class SessionConfig(BaseModel):
    reset: SessionResetConfig = Field(default_factory=SessionResetConfig)


class AgentInfo(BaseModel):
    id: str = "main"
    name: str = "Selma"
    model: str = ""
    workspace: str = "."
    # "all" → all tools allowed; list → only the named tools
    toolsAllow: Union[str, List[str]] = "all"
    model_config = ConfigDict(extra='allow')

    @field_validator("toolsAllow", mode="before")
    @classmethod
    def validate_tools_allow(cls, v: Any) -> Union[str, List[str]]:
        if v == "all":
            return v
        if isinstance(v, list):
            return [str(item) for item in v]
        raise ValueError("toolsAllow must be 'all' or a list of tool names")


# -- Heartbeat sub-models --------------------------

class ActiveHoursConfig(BaseModel):
    start: str = "00:00"    # "HH:MM" 24h
    end: str = "23:59"
    timezone: str = "UTC"


class HeartbeatConfig(BaseModel):
    every: str = "0m"               # "0m" = disabled; e.g. "30m", "1h"
    target: str = "none"            # "none" | "last"
    light_context: bool = False     # inject only HEARTBEAT.md
    isolated_session: bool = False  # fresh session per run
    ack_max_chars: int = 300
    active_hours: ActiveHoursConfig | None = None


# -- Memory sub-model ------------------------------

class MemoryConfig(BaseModel):
    vector_search: bool = Field(
        default=False,
        description=(
            "Enables hybrid search (vector similarity + FTS5). "
            "Requires a running Ollama embedding endpoint. "
            "False → FTS5 keyword search only, no embedding calls."
        ),
    )
    embed_model: str = Field(
        default="nomic-embed-text",
        description=(
            "Ollama embedding model name. "
            "Only used when vector_search=True. "
            "The endpoint URL is read from config.model.ollama_base_url at runtime."
        ),
    )
    temporal_decay: bool = Field(
        default=False,
        description=(
            "Boosts newer memory entries in ranking. "
            "score = (1 − DECAY_WEIGHT) × relevance + DECAY_WEIGHT × e^(−λ × age_days). "
            "DECAY_WEIGHT is hardcoded to 0.3."
        ),
    )
    temporal_decay_rate: float = Field(
        default=0.01,
        description=(
            "Decay rate λ per day (only relevant when temporal_decay=True). "
            "decay = e^(−λ × age_days). "
            "Examples: λ=0.01 → 100 days ≈ 0.37, 200 days ≈ 0.14. "
            "λ=0.1 → 10 days ≈ 0.37 (faster decay)."
        ),
    )


# -- Main config -----------------------------------

class SelmaConfig(BaseModel):
    """
    Loads from .selma/selma.json.

    Explicit fields for channels, routing, and agents give ChannelRouter
    typed attribute access — same interface as the old Config class in data.py,
    so no changes are needed there.
    extra='allow' keeps all additional fields without a fixed schema accessible.
    """
    model_config = ConfigDict(extra='allow')

    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    # Single agent configuration
    agent: AgentInfo = Field(default_factory=AgentInfo)

    def is_channel_enabled(self, channel_name: str) -> bool:
        channel = getattr(self.channels, channel_name, None)
        return channel.enabled if channel else False

    def get_telegram_token(self) -> Optional[str]:
        # Token is not in the config — read from TELEGRAM_TOKEN in .env
        return os.environ.get("TELEGRAM_TOKEN")


# -- Cache Storage (Module Level) -----------------
CACHE_VALIDITY_SECONDS = 120

# Keyed by resolved config path so different workspaces don't share a cache entry.
_config_cache: Dict[str, tuple[SelmaConfig, float]] = {}


# -- Functions -------------------------------

def load_config(cwd: str = ".", cache: bool = True) -> SelmaConfig:
    """
    Loads the configuration from selma.json in the resolved state directory.

    The state directory is resolved via resolve_state_dir() (SELMA_STATE_DIR →
    .selma in cwd → ~/.selma).
    Implements a 120-second cache mechanism keyed on the resolved config path.
    If the file is missing, it raises a FileNotFoundError.
    """
    current_time = time.time()
    config_path = resolve_state_dir(cwd) / "selma.json"
    cache_key = str(config_path.resolve())

    # Check cache validity
    if cache and cache_key in _config_cache:
        cached_config, cached_time = _config_cache[cache_key]
        if (current_time - cached_time) < CACHE_VALIDITY_SECONDS:
            return cached_config

    # Check if file exists
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at: {config_path.absolute()}")

    # Load and parse the file
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Create object dynamically using the 'extra=allow' policy
        config_obj = SelmaConfig(**data)

        # Update cache metadata
        _config_cache[cache_key] = (config_obj, current_time)

        return config_obj

    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON in {config_path}: {e}")
    except Exception as e:
        raise RuntimeError(f"An unexpected error occurred while loading config: {e}")


def get_default_model(config: SelmaConfig) -> tuple[str, str]:
    """
    Reads provider and model from the configuration.
    Returns (provider, model).

    Reads config.model.model — expects format "provider/model",
    e.g. "ollama/llama3.1" → ("ollama", "llama3.1").

    If model.model contains no "/", "ollama" is assumed as provider
    and the entire value is treated as the model name.

    Corresponds to resolveDefaultModelForAgent() in OpenClaw
    (src/agents/pi-embedded-runner/run.ts).
    """
    model_str = config.model.model.strip()
    if not model_str:
        return "ollama", "llama3.2"

    if "/" in model_str:
        provider, _, model = model_str.partition("/")
        return provider.strip(), model.strip()

    return "ollama", model_str


def resolve_timeout(config: SelmaConfig, fallback: int = 60) -> int:
    """
    Reads timeout_seconds from config.model.timeout_seconds.
    Returns fallback if not configured.
    """
    return config.model.timeout_seconds


def resolve_tools_allow(config: SelmaConfig) -> list[str] | None:
    """
    Returns the resolved tool allowlist for the configured agent.

    Returns None when all tools are allowed ("all").
    Returns a list of tool name strings when a specific allowlist is configured.
    """
    tools_allow = config.agent.toolsAllow
    if tools_allow == "all":
        return None
    return list(tools_allow)


def resolve_thinking_default(
    config: SelmaConfig,
    provider: str,
    model: str,
) -> str | None:
    """
    Determines the default thinking level for a model.

    Reads config.model.thinking — allowed values: "low", "medium", "high".
    Falls back to None when:
      - provider is "ollama" (local models do not support reasoning_effort)

    Falls back to "low" when:
      - no value is configured
      - the value is unknown

    Corresponds to the thinking-level resolution in runEmbeddedPiAgent()
    in OpenClaw (src/agents/pi-embedded-runner/run.ts).
    """
    # Ollama models don't support thinking/reasoning_effort
    if provider.lower() == "ollama":
        return None

    thinking = config.model.thinking.strip().lower()
    if thinking in THINKING_LEVELS:
        return thinking

    return "low"
