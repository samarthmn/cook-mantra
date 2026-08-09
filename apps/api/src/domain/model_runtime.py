"""Provider-neutral model runtime contracts shared by agents and adapters."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ProviderName(StrEnum):
    """Model providers selectable by a local Cook Mantra installation."""

    OLLAMA = "ollama"
    OPENROUTER = "openrouter"
    CODEX = "codex"


IMAGE_PROVIDER_NAMES = frozenset({ProviderName.OLLAMA, ProviderName.OPENROUTER})


class Capability(StrEnum):
    """Operations a provider/model pair can authoritatively advertise."""

    TEXT = "text"
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
    IMAGE_OUTPUT = "image_output"


class AgentRole(StrEnum):
    """Code-owned Cook Mantra roles, independent of provider terminology."""

    INGREDIENT_EXTRACTOR = "ingredient_extractor"
    MASTER_CHEF = "master_chef"
    RECIPE_WRITER = "recipe_writer"
    IMAGE_GENERATOR = "image_generator"

    @property
    def required_capabilities(self) -> frozenset[Capability]:
        """Return the complete capability contract owned by application code."""
        return _ROLE_CAPABILITIES[self]

    @property
    def required(self) -> bool:
        """Whether this role must be ready for the core cooking journey."""
        return self is not AgentRole.IMAGE_GENERATOR


_ROLE_CAPABILITIES: dict[AgentRole, frozenset[Capability]] = {
    AgentRole.INGREDIENT_EXTRACTOR: frozenset(
        {Capability.VISION, Capability.STRUCTURED_OUTPUT}
    ),
    AgentRole.MASTER_CHEF: frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
    AgentRole.RECIPE_WRITER: frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
    AgentRole.IMAGE_GENERATOR: frozenset({Capability.IMAGE_OUTPUT}),
}


class ReasoningEffort(StrEnum):
    """Provider-neutral bounded reasoning preference for text generation."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TextTuning(BaseModel):
    """Provider-neutral text tuning accepted from local YAML."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    reasoning_effort: ReasoningEffort | None = None
    max_output_tokens: int | None = Field(default=None, ge=1, le=131_072)
    context_window: int | None = Field(default=None, ge=4_096, le=1_048_576)
    timeout_seconds: float | None = Field(default=None, gt=0, le=3_600)


class ImageOutputFormat(StrEnum):
    """Validated image encodings supported by the runtime contract."""

    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


class ImageQuality(StrEnum):
    """Provider-neutral image quality preference."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ImageTuning(BaseModel):
    """Provider-neutral image generation tuning accepted from local YAML."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(default=1024, ge=256, le=2_048)
    height: int = Field(default=1024, ge=256, le=2_048)
    quality: ImageQuality = ImageQuality.MEDIUM
    output_format: ImageOutputFormat = ImageOutputFormat.WEBP
    timeout_seconds: float = Field(default=600.0, gt=0, le=3_600)


class ModelUsage(BaseModel):
    """Normalized usage returned by any provider adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelUsageEvent(BaseModel):
    """Content-free usage plus bounded invocation correlation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    provider: ProviderName
    model: str
    usage: ModelUsage
    session_id: str | None = None
    job_id: str | None = None
    batch_number: int | None = Field(default=None, ge=1)


@dataclass(frozen=True, slots=True)
class ModelResult[OutputT]:
    """Normalized provider result paired with optional token usage."""

    output: OutputT
    usage: ModelUsage | None = None


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """Canonical provider-neutral chat input with redacted binary diagnostics."""

    role: str
    content: str
    image: bytes | None = field(default=None, repr=False)
    media_type: str | None = None

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ValueError("model message role is invalid")
        if not self.content:
            raise ValueError("model message content must not be empty")
        if (self.image is None) != (self.media_type is None):
            raise ValueError("image and media_type must be supplied together")


@dataclass(frozen=True, slots=True)
class ProviderImageOutput:
    """Unverified image transport output with sensitive payload diagnostics."""

    base64_data: str = field(repr=False)
    media_type: str | None = None


class TextProvider(Protocol):
    """Transport-neutral plain-text generation boundary."""

    async def generate_text(self, prompt: str) -> ModelResult[str]: ...


class VisionProvider[OutputT](Protocol):
    """Transport-neutral image-understanding boundary."""

    async def analyze_image(
        self,
        image: bytes,
        *,
        media_type: str,
        prompt: str,
    ) -> ModelResult[OutputT]: ...


class StructuredOutputProvider[OutputT](Protocol):
    """Transport-neutral structured-output boundary."""

    async def generate_structured(
        self,
        prompt: str,
        *,
        schema: type[OutputT],
    ) -> ModelResult[OutputT]: ...


class ImageOutputProvider(Protocol):
    """Transport-neutral image-generation boundary."""

    async def generate_image(
        self,
        prompt: str,
        *,
        tuning: ImageTuning,
    ) -> ModelResult[ProviderImageOutput]: ...


class ProviderErrorKind(StrEnum):
    """Provider failures normalized before reaching application workflows."""

    UNAVAILABLE = "unavailable"
    AUTHENTICATION_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    PAYMENT_REQUIRED = "payment_required"
    CAPABILITY_MISSING = "capability_missing"
    PROTOCOL_ERROR = "protocol_error"
    TIMED_OUT = "timed_out"
    INVALID_OUTPUT = "invalid_output"


class ProviderError(BaseModel):
    """Safe normalized provider failure; never carries raw responses or secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ProviderErrorKind
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    provider: ProviderName | None = None
    role: AgentRole | None = None


class DiscoveredModel(BaseModel):
    """Safe immutable discovery result for one exact configured model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    available: bool
    capabilities: tuple[Capability, ...] = ()
    supported_parameters: tuple[str, ...] = ()
    context_window: int | None = Field(default=None, ge=1)
    error: ProviderErrorKind | None = None


class ProviderDiscoveryResult(BaseModel):
    """Safe provider inventory restricted to configured model identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName
    models: dict[str, DiscoveredModel]
    available_models: tuple[str, ...] = ()


class RoleReadiness(BaseModel):
    """Authoritative readiness for one configured application role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    provider: ProviderName | None
    model: str | None
    enabled: bool
    ready: bool
    required_capabilities: tuple[Capability, ...]
    available_capabilities: tuple[Capability, ...]
    error: ProviderErrorKind | None = None


class ModelRuntimeReadiness(BaseModel):
    """Process-cached readiness snapshot for every configured role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    roles: dict[AgentRole, RoleReadiness] = Field(
        min_length=len(AgentRole),
        max_length=len(AgentRole),
    )
