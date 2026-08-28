import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from open_notebook.course.contracts import (
    ChapterArtifact,
    LabSpecVariant,
    ModelSelection,
    ProvenanceLabel,
    SafeLabKey,
    ValidationFinding,
)
from open_notebook.course.v2_contracts import (
    AnswerType,
    ConceptMastery,
    CourseBundleManifest,
    DifficultyVector,
    DraftOperation,
    ExerciseBlueprint,
    ExerciseVerification,
    GradeResult,
    LearningEvent,
    LearningEventPayload,
    PositionPayload,
    ReviewQueueItem,
    Sha256,
    StableKey,
    TransferDimension,
    TutorResponse,
    TutorTurn,
    ValidationCheck,
)


# Notebook models
class NotebookCreate(BaseModel):
    name: str = Field(..., description="Name of the notebook")
    description: str = Field(default="", description="Description of the notebook")


class NotebookUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Name of the notebook")
    description: Optional[str] = Field(None, description="Description of the notebook")
    archived: Optional[bool] = Field(
        None, description="Whether the notebook is archived"
    )


class NotebookResponse(BaseModel):
    id: str
    name: str
    description: str
    archived: bool
    created: str
    updated: str
    source_count: int
    note_count: int


class RecentlyViewedResponse(BaseModel):
    type: Literal["notebook", "source"]
    id: str
    title: str
    last_viewed_at: str


# Search models
class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query")
    type: Literal["text", "vector"] = Field("text", description="Search type")
    limit: int = Field(100, description="Maximum number of results", ge=1, le=1000)
    search_sources: bool = Field(True, description="Include sources in search")
    search_notes: bool = Field(True, description="Include notes in search")
    minimum_score: float = Field(
        0.2, description="Minimum score for vector search", ge=0, le=1
    )


class SearchResponse(BaseModel):
    results: List[Dict[str, Any]] = Field(..., description="Search results")
    total_count: int = Field(..., description="Total number of results")
    search_type: str = Field(..., description="Type of search performed")


class AskRequest(BaseModel):
    question: str = Field(..., description="Question to ask the knowledge base")
    strategy_model: str = Field(..., description="Model ID for query strategy")
    answer_model: str = Field(..., description="Model ID for individual answers")
    final_answer_model: str = Field(..., description="Model ID for final answer")


class AskResponse(BaseModel):
    answer: str = Field(..., description="Final answer from the knowledge base")
    question: str = Field(..., description="Original question")


# Models API models
class ModelCreate(BaseModel):
    name: str = Field(..., description="Model name (e.g., gpt-5-mini, claude, gemini)")
    provider: str = Field(
        ..., description="Provider name (e.g., openai, anthropic, gemini)"
    )
    type: str = Field(
        ...,
        description="Model type (language, embedding, text_to_speech, speech_to_text)",
    )
    credential: Optional[str] = Field(
        None, description="Credential ID to link this model to"
    )


class ModelResponse(BaseModel):
    id: str
    name: str
    provider: str
    type: str
    credential: Optional[str] = None
    created: str
    updated: str


class DefaultModelsResponse(BaseModel):
    default_chat_model: Optional[str] = None
    default_transformation_model: Optional[str] = None
    large_context_model: Optional[str] = None
    default_text_to_speech_model: Optional[str] = None
    default_speech_to_text_model: Optional[str] = None
    default_embedding_model: Optional[str] = None
    default_tools_model: Optional[str] = None


class ProviderAvailabilityResponse(BaseModel):
    available: List[str] = Field(..., description="List of available providers")
    unavailable: List[str] = Field(..., description="List of unavailable providers")
    supported_types: Dict[str, List[str]] = Field(
        ..., description="Provider to supported model types mapping"
    )


# Transformations API models
class TransformationCreate(BaseModel):
    name: str = Field(..., description="Transformation name")
    title: str = Field(..., description="Display title for the transformation")
    description: str = Field(
        ..., description="Description of what this transformation does"
    )
    prompt: str = Field(..., description="The transformation prompt")
    apply_default: bool = Field(
        False, description="Whether to apply this transformation by default"
    )
    model_id: Optional[str] = Field(
        None, description="Model ID to use by default for this transformation"
    )


class TransformationUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Transformation name")
    title: Optional[str] = Field(
        None, description="Display title for the transformation"
    )
    description: Optional[str] = Field(
        None, description="Description of what this transformation does"
    )
    prompt: Optional[str] = Field(None, description="The transformation prompt")
    apply_default: Optional[bool] = Field(
        None, description="Whether to apply this transformation by default"
    )
    model_id: Optional[str] = Field(
        None, description="Model ID to use by default for this transformation"
    )


class TransformationResponse(BaseModel):
    id: str
    name: str
    title: str
    description: str
    prompt: str
    apply_default: bool
    model_id: Optional[str] = None
    created: str
    updated: str


class TransformationExecuteRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    transformation_id: str = Field(
        ..., description="ID of the transformation to execute"
    )
    input_text: str = Field(..., description="Text to transform")
    model_id: Optional[str] = Field(
        None, description="Model ID to use for this transformation run"
    )


class TransformationExecuteResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    output: str = Field(..., description="Transformed text")
    transformation_id: str = Field(..., description="ID of the transformation used")
    model_id: Optional[str] = Field(None, description="Model ID used")


# Default Prompt API models
class DefaultPromptResponse(BaseModel):
    transformation_instructions: str = Field(
        ..., description="Default transformation instructions"
    )


class DefaultPromptUpdate(BaseModel):
    transformation_instructions: str = Field(
        ..., description="Default transformation instructions"
    )


# Notes API models
class NoteCreate(BaseModel):
    title: Optional[str] = Field(None, description="Note title")
    content: str = Field(..., description="Note content")
    note_type: Optional[str] = Field("human", description="Type of note (human, ai)")
    notebook_id: Optional[str] = Field(
        None, description="Notebook ID to add the note to"
    )


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, description="Note title")
    content: Optional[str] = Field(None, description="Note content")
    note_type: Optional[str] = Field(None, description="Type of note (human, ai)")


class NoteResponse(BaseModel):
    id: str
    title: Optional[str]
    content: Optional[str]
    note_type: Optional[str]
    created: str
    updated: str
    command_id: Optional[str] = None


# Embedding API models
class EmbedRequest(BaseModel):
    item_id: str = Field(..., description="ID of the item to embed")
    item_type: str = Field(..., description="Type of item (source, note)")
    async_processing: bool = Field(
        False, description="Process asynchronously in background"
    )


class EmbedResponse(BaseModel):
    success: bool = Field(..., description="Whether embedding was successful")
    message: str = Field(..., description="Result message")
    item_id: str = Field(..., description="ID of the item that was embedded")
    item_type: str = Field(..., description="Type of item that was embedded")
    command_id: Optional[str] = Field(
        None, description="Command ID for async processing"
    )


# Rebuild request/response models
class RebuildRequest(BaseModel):
    mode: Literal["existing", "all"] = Field(
        ...,
        description="Rebuild mode: 'existing' only re-embeds items with embeddings, 'all' embeds everything",
    )
    include_sources: bool = Field(True, description="Include sources in rebuild")
    include_notes: bool = Field(True, description="Include notes in rebuild")
    include_insights: bool = Field(True, description="Include insights in rebuild")


class RebuildResponse(BaseModel):
    command_id: str = Field(..., description="Command ID to track progress")
    total_items: int = Field(..., description="Estimated number of items to process")
    message: str = Field(..., description="Status message")


class RebuildProgress(BaseModel):
    processed: int = Field(..., description="Number of items processed")
    total: int = Field(..., description="Total items to process")
    percentage: float = Field(..., description="Progress percentage")


class RebuildStats(BaseModel):
    sources: int = Field(0, description="Sources processed")
    notes: int = Field(0, description="Notes processed")
    insights: int = Field(0, description="Insights processed")
    failed: int = Field(0, description="Failed items")


class RebuildStatusResponse(BaseModel):
    command_id: str = Field(..., description="Command ID")
    status: str = Field(..., description="Status: queued, running, completed, failed")
    progress: Optional[RebuildProgress] = None
    stats: Optional[RebuildStats] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


# Settings API models
class SettingsResponse(BaseModel):
    default_content_processing_engine_doc: Optional[str] = None
    default_content_processing_engine_url: Optional[str] = None
    default_embedding_option: Optional[str] = None
    auto_delete_files: Optional[str] = None
    docling_ocr: Optional[bool] = None
    docling_formulas: Optional[bool] = None
    docling_vision: Optional[bool] = None
    youtube_preferred_languages: Optional[List[str]] = None


class SettingsUpdate(BaseModel):
    default_content_processing_engine_doc: Optional[str] = None
    default_content_processing_engine_url: Optional[str] = None
    default_embedding_option: Optional[str] = None
    auto_delete_files: Optional[str] = None
    docling_ocr: Optional[bool] = None
    docling_formulas: Optional[bool] = None
    docling_vision: Optional[bool] = None
    youtube_preferred_languages: Optional[List[str]] = None


# Sources API models
class AssetModel(BaseModel):
    file_path: Optional[str] = None
    url: Optional[str] = None


class SourceCreate(BaseModel):
    # Backward compatibility: support old single notebook_id
    notebook_id: Optional[str] = Field(
        None, description="Notebook ID to add the source to (deprecated, use notebooks)"
    )
    # New multi-notebook support
    notebooks: Optional[List[str]] = Field(
        None,
        max_length=50,
        description="List of notebook IDs to add the source to (max 50)",
    )
    # Required fields
    type: str = Field(..., description="Source type: link, upload, or text")
    url: Optional[str] = Field(None, description="URL for link type")
    file_path: Optional[str] = Field(None, description="File path for upload type")
    content: Optional[str] = Field(None, description="Text content for text type")
    title: Optional[str] = Field(None, description="Source title")
    transformations: Optional[List[str]] = Field(
        default_factory=list,
        max_length=50,
        description="Transformation IDs to apply (max 50)",
    )
    embed: bool = Field(False, description="Whether to embed content for vector search")
    delete_source: bool = Field(
        False, description="Whether to delete uploaded file after processing"
    )
    # New async processing support
    async_processing: bool = Field(
        False, description="Whether to process source asynchronously"
    )

    @model_validator(mode="after")
    def validate_notebook_fields(self):
        # Ensure only one of notebook_id or notebooks is provided
        if self.notebook_id is not None and self.notebooks is not None:
            raise ValueError(
                "Cannot specify both 'notebook_id' and 'notebooks'. Use 'notebooks' for multi-notebook support."
            )

        # Convert single notebook_id to notebooks array for internal processing
        if self.notebook_id is not None:
            self.notebooks = [self.notebook_id]
            # Keep notebook_id for backward compatibility in response

        # Set empty array if no notebooks specified (allow sources without notebooks)
        if self.notebooks is None:
            self.notebooks = []

        return self


class SourceUpdate(BaseModel):
    title: Optional[str] = Field(None, description="Source title")
    topics: Optional[List[str]] = Field(None, description="Source topics")


class SourceResponse(BaseModel):
    id: str
    title: Optional[str]
    topics: Optional[List[str]]
    asset: Optional[AssetModel]
    full_text: Optional[str]
    embedded: bool
    embedded_chunks: int
    file_available: Optional[bool] = None
    created: str
    updated: str
    # New fields for async processing
    command_id: Optional[str] = None
    status: Optional[str] = None
    processing_info: Optional[Dict] = None
    # Notebook associations
    notebooks: Optional[List[str]] = None


class SourceListResponse(BaseModel):
    id: str
    title: Optional[str]
    topics: Optional[List[str]]
    asset: Optional[AssetModel]
    embedded: bool  # Boolean flag indicating if source has embeddings
    embedded_chunks: int  # Number of embedded chunks
    insights_count: int
    created: str
    updated: str
    file_available: Optional[bool] = None
    # Status fields for async processing
    command_id: Optional[str] = None
    status: Optional[str] = None
    processing_info: Optional[Dict[str, Any]] = None


# Insights API models
class SourceInsightResponse(BaseModel):
    id: str
    source_id: str
    insight_type: str
    content: str
    # Optional: insights created before migration 19 have no timestamps,
    # and the API must return null for them (never the string "None").
    created: Optional[str] = None
    updated: Optional[str] = None


class InsightCreationResponse(BaseModel):
    """Response for async insight creation."""

    status: Literal["pending"] = "pending"
    message: str = "Insight generation started"
    source_id: str
    transformation_id: str
    command_id: Optional[str] = None


class SaveAsNoteRequest(BaseModel):
    notebook_id: Optional[str] = Field(None, description="Notebook ID to add note to")


class CreateSourceInsightRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    transformation_id: str = Field(..., description="ID of transformation to apply")
    model_id: Optional[str] = Field(
        None, description="Model ID (uses default if not provided)"
    )


# Source status response
class SourceStatusResponse(BaseModel):
    status: Optional[str] = Field(None, description="Processing status")
    message: str = Field(..., description="Descriptive message about the status")
    processing_info: Optional[Dict[str, Any]] = Field(
        None, description="Detailed processing information"
    )
    command_id: Optional[str] = Field(None, description="Command ID if available")


# Error response
class ErrorResponse(BaseModel):
    error: str
    message: str


# API Key Configuration models
class SetApiKeyRequest(BaseModel):
    """Request to set an API key for a provider."""

    api_key: Optional[str] = Field(None, description="API key for the provider")
    base_url: Optional[str] = Field(
        None, description="Base URL for URL-based providers (Ollama, OpenAI-compatible)"
    )
    endpoint: Optional[str] = Field(None, description="Endpoint URL for Azure OpenAI")
    api_version: Optional[str] = Field(None, description="API version for Azure OpenAI")
    endpoint_llm: Optional[str] = Field(
        None, description="Service-specific endpoint for LLM (Azure)"
    )
    endpoint_embedding: Optional[str] = Field(
        None, description="Service-specific endpoint for embedding (Azure)"
    )
    endpoint_stt: Optional[str] = Field(
        None, description="Service-specific endpoint for STT (Azure)"
    )
    endpoint_tts: Optional[str] = Field(
        None, description="Service-specific endpoint for TTS (Azure)"
    )
    service_type: Optional[Literal["llm", "embedding", "stt", "tts"]] = Field(
        None,
        description="Service type for OpenAI-compatible providers (llm, embedding, stt, tts)",
    )
    # Vertex AI specific fields
    vertex_project: Optional[str] = Field(
        None, description="Google Cloud Project ID for Vertex AI"
    )
    vertex_location: Optional[str] = Field(
        None, description="Google Cloud Region for Vertex AI (e.g., us-central1)"
    )
    vertex_credentials_path: Optional[str] = Field(
        None, description="Path to Google Cloud service account JSON file"
    )

    @field_validator(
        "api_key",
        "base_url",
        "endpoint",
        "api_version",
        "endpoint_llm",
        "endpoint_embedding",
        "endpoint_stt",
        "endpoint_tts",
        "vertex_project",
        "vertex_location",
        "vertex_credentials_path",
        mode="before",
    )
    @classmethod
    def validate_not_empty_string(cls, v: Optional[str]) -> Optional[str]:
        """Reject empty strings - convert to None or raise error."""
        if v is not None:
            stripped = v.strip()
            if not stripped:
                return None  # Treat empty/whitespace-only as None
            return stripped
        return v


class ApiKeyStatusResponse(BaseModel):
    """Response showing which providers are configured and their source."""

    configured: Dict[str, bool] = Field(
        ..., description="Map of provider name to whether it is configured"
    )
    source: Dict[str, Literal["database", "environment", "none"]] = Field(
        ...,
        description="Map of provider name to configuration source (database, environment, or none)",
    )
    encryption_configured: bool = Field(
        ...,
        description="Whether OPEN_NOTEBOOK_ENCRYPTION_KEY is set (required to store keys in database)",
    )


class TestConnectionResponse(BaseModel):
    """Response from testing a provider connection."""

    provider: str = Field(..., description="Provider name that was tested")
    success: bool = Field(..., description="Whether connection test succeeded")
    message: str = Field(..., description="Result message with details")


class MigrateFromEnvRequest(BaseModel):
    """Request to migrate API keys from environment variables to database."""

    force: bool = Field(
        False, description="Force overwrite existing database configurations"
    )


class MigrationResult(BaseModel):
    """Response from migrating API keys from environment to database."""

    message: str = Field(..., description="Summary message")
    migrated: List[str] = Field(
        default_factory=list, description="Providers successfully migrated"
    )
    skipped: List[str] = Field(
        default_factory=list, description="Providers skipped (already in DB)"
    )
    errors: List[str] = Field(
        default_factory=list, description="Migration errors by provider"
    )


# Notebook delete cascade models
# Credential models

# Kept in sync with the provider registry
# (open_notebook/ai/provider_registry.py PROVIDERS — the backend source of
# truth). A Literal can't be built at runtime, so this is the one remaining
# manual copy; tests/test_credential_provider_validation.py enforces the sync.
# The frontend consumes GET /api/providers at runtime and needs no edit.
SupportedProvider = Literal[
    "openai",
    "anthropic",
    "google",
    "groq",
    "mistral",
    "deepseek",
    "xai",
    "openrouter",
    "dashscope",
    "minimax",
    "novita",
    "ppq",
    "cohere",
    "voyage",
    "elevenlabs",
    "deepgram",
    "ollama",
    "omlx",
    "azure",
    "vertex",
    "openai_compatible",
    "anthropic_compatible",
]


class ProviderInfoResponse(BaseModel):
    """Provider metadata from the provider registry."""

    name: str = Field(..., description="Provider identifier (e.g. openai)")
    display_name: str = Field(..., description="Human-friendly provider name")
    modalities: List[str] = Field(
        ..., description="Default modalities supported by the provider"
    )
    docs_url: Optional[str] = Field(
        None, description="Where to get an API key / set the provider up"
    )
    env_configured: bool = Field(
        ..., description="Whether the provider is configured via environment variables"
    )


class CapabilitiesResponse(BaseModel):
    """Runtime availability of the opt-in heavy extraction engines.

    Reflects what is actually importable/reachable in this container — not merely
    what the OPEN_NOTEBOOK_ENABLE_* flags request — so the UI can gate engine
    options honestly (e.g. still show "unavailable" while a first-boot install
    is in progress). See docs/7-DEVELOPMENT/decisions/ADR-007-optin-runtimes.md.
    """

    docling_available: bool = Field(
        ...,
        description="Docling is installed: the docling document engine, OCR toggle and image sources work.",
    )
    crawl4ai_available: bool = Field(
        ...,
        description="Crawl4AI is usable: the local package is installed OR a remote server is configured.",
    )
    crawl4ai_remote_configured: bool = Field(
        ...,
        description="A remote Crawl4AI endpoint is configured via CRAWL4AI_API_URL (no local install needed).",
    )


def validate_url_key_provider_required_fields(
    provider: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
) -> None:
    """Shared required-field rule for providers that need BOTH a base URL and an
    API key (currently anthropic_compatible).

    Called from both the create path (CreateCredentialRequest validator, which sees
    the full request payload) and the update path
    (credentials_service.ensure_provider_required_fields, which runs against the
    merged credential). Raises ValueError when a required field is missing.
    """
    if (provider or "").lower() == "anthropic_compatible":
        if not base_url or not str(base_url).strip():
            raise ValueError("Anthropic-compatible credentials require a base URL")
        if not api_key or not str(api_key).strip():
            raise ValueError("Anthropic-compatible credentials require an API key")


class CreateCredentialRequest(BaseModel):
    """Request to create a new credential."""

    name: str = Field(..., description="Credential name")
    provider: SupportedProvider = Field(
        ..., description="Provider name (openai, anthropic, etc.)"
    )
    modalities: List[str] = Field(
        default_factory=list,
        description="Supported modalities (language, embedding, text_to_speech, speech_to_text)",
    )
    api_key: Optional[str] = Field(None, description="API key (stored encrypted)")
    base_url: Optional[str] = Field(None, description="Base URL")
    endpoint: Optional[str] = Field(None, description="Endpoint URL (Azure)")
    api_version: Optional[str] = Field(None, description="API version (Azure)")
    endpoint_llm: Optional[str] = Field(None, description="LLM endpoint")
    endpoint_embedding: Optional[str] = Field(None, description="Embedding endpoint")
    endpoint_stt: Optional[str] = Field(None, description="STT endpoint")
    endpoint_tts: Optional[str] = Field(None, description="TTS endpoint")
    project: Optional[str] = Field(None, description="Project ID (Vertex)")
    location: Optional[str] = Field(None, description="Location (Vertex)")
    credentials_path: Optional[str] = Field(
        None, description="Credentials file path (Vertex)"
    )
    num_ctx: Optional[int] = Field(
        None, description="Context window size (Ollama only; defaults to 8192)"
    )

    @model_validator(mode="after")
    def _validate_provider_required_fields(self):
        validate_url_key_provider_required_fields(
            self.provider, self.base_url, self.api_key
        )
        return self


class UpdateCredentialRequest(BaseModel):
    """Request to update an existing credential."""

    name: Optional[str] = Field(None, description="Credential name")
    modalities: Optional[List[str]] = Field(None, description="Supported modalities")
    api_key: Optional[str] = Field(None, description="API key (stored encrypted)")
    base_url: Optional[str] = Field(None, description="Base URL")
    endpoint: Optional[str] = Field(None, description="Endpoint URL")
    api_version: Optional[str] = Field(None, description="API version")
    endpoint_llm: Optional[str] = Field(None, description="LLM endpoint")
    endpoint_embedding: Optional[str] = Field(None, description="Embedding endpoint")
    endpoint_stt: Optional[str] = Field(None, description="STT endpoint")
    endpoint_tts: Optional[str] = Field(None, description="TTS endpoint")
    project: Optional[str] = Field(None, description="Project ID")
    location: Optional[str] = Field(None, description="Location")
    credentials_path: Optional[str] = Field(None, description="Credentials path")
    num_ctx: Optional[int] = Field(
        None, description="Context window size (Ollama only; defaults to 8192)"
    )


class CredentialResponse(BaseModel):
    """Response for a credential (never includes api_key)."""

    id: str
    name: str
    provider: str
    modalities: List[str]
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    api_version: Optional[str] = None
    endpoint_llm: Optional[str] = None
    endpoint_embedding: Optional[str] = None
    endpoint_stt: Optional[str] = None
    endpoint_tts: Optional[str] = None
    project: Optional[str] = None
    location: Optional[str] = None
    credentials_path: Optional[str] = None
    num_ctx: Optional[int] = None
    has_api_key: bool = False
    created: str
    updated: str
    model_count: int = 0
    decryption_error: Optional[str] = None


class CredentialDeleteResponse(BaseModel):
    """Response for credential deletion."""

    message: str
    deleted_models: int = 0


class DiscoveredModelResponse(BaseModel):
    """A model discovered from a provider."""

    name: str
    provider: str
    model_type: Optional[str] = None
    description: Optional[str] = None


class DiscoverModelsResponse(BaseModel):
    """Response from model discovery."""

    credential_id: str
    provider: str
    discovered: List[DiscoveredModelResponse]


class RegisterModelData(BaseModel):
    """A model to register with user-specified type."""

    name: str
    provider: str
    model_type: str  # Required: user specifies the type


class RegisterModelsRequest(BaseModel):
    """Request to register discovered models."""

    models: List[RegisterModelData]


class RegisterModelsResponse(BaseModel):
    """Response from model registration."""

    created: int
    existing: int


class NotebookDeletePreview(BaseModel):
    notebook_id: str = Field(..., description="ID of the notebook")
    notebook_name: str = Field(..., description="Name of the notebook")
    note_count: int = Field(..., description="Number of notes that will be deleted")
    exclusive_source_count: int = Field(
        ..., description="Number of sources only in this notebook"
    )
    shared_source_count: int = Field(
        ..., description="Number of sources shared with other notebooks"
    )


class NotebookDeleteResponse(BaseModel):
    message: str = Field(..., description="Success message")
    deleted_notes: int = Field(..., description="Number of notes deleted")
    deleted_sources: int = Field(..., description="Number of exclusive sources deleted")
    unlinked_sources: int = Field(
        ..., description="Number of sources unlinked from notebook"
    )
    deleted_chat_sessions: int = Field(
        ..., description="Number of chat sessions deleted"
    )


# Course module models (PDR-003)


class StrictCourseRequest(BaseModel):
    """Reject unknown Course fields at the HTTP trust boundary."""

    model_config = ConfigDict(extra="forbid")


def _bounded_json_answer(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("answer must be valid JSON") from exc
    if len(encoded.encode("utf-8")) > 32_000:
        raise ValueError("answer is too large")
    return value


CourseClientLearningEventKind = Literal[
    "chapter_opened",
    "hint_viewed",
    "answer_revealed",
    "transfer_required",
    "transfer_completed",
    "reading_position",
]


class CourseLearningEventRequest(StrictCourseRequest):
    """Client-authored action; Course ownership is added by the server."""

    snapshot_token: Sha256
    idempotency_key: StableKey
    chapter_key: StableKey
    concept_key: StableKey | None = None
    exercise_key: StableKey | None = None
    kind: CourseClientLearningEventKind
    payload: LearningEventPayload

    @model_validator(mode="after")
    def transition_shape_is_valid(self) -> "CourseLearningEventRequest":
        is_activity = self.kind in {"chapter_opened", "reading_position"}
        if is_activity:
            if self.concept_key is not None or self.exercise_key is not None:
                raise ValueError("activity events cannot claim a concept or exercise")
        elif self.concept_key is None or self.exercise_key is None:
            raise ValueError(
                "exercise events require concept_key and exercise_key stable keys"
            )
        LearningEvent(
            event_id="action-request",
            course_id="course:request",
            course_version_id="course_version:request",
            chapter_key=self.chapter_key,
            concept_key=self.concept_key,
            exercise_key=self.exercise_key,
            kind=self.kind,
            payload=self.payload,
            occurred_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        return self


class CourseActivityEventRequest(StrictCourseRequest):
    """Only non-mastery reading activity may use the generic public event route."""

    snapshot_token: Sha256
    idempotency_key: StableKey
    chapter_key: StableKey
    kind: Literal["chapter_opened", "reading_position"]
    payload: PositionPayload

    @model_validator(mode="after")
    def reading_position_has_a_block(self) -> "CourseActivityEventRequest":
        if self.kind == "reading_position" and self.payload.block_key is None:
            raise ValueError("reading_position requires a stable block key")
        return self


class CourseExerciseGradeRequest(StrictCourseRequest):
    snapshot_token: Sha256
    chapter_key: StableKey
    concept_key: StableKey
    attempt_key: StableKey
    answer: Any
    hints_used: int = Field(default=0, ge=0, le=4)
    answer_revealed: bool = False
    mode: Literal["practice", "review"] = "practice"

    @field_validator("answer")
    @classmethod
    def answer_is_bounded_json(cls, value: Any) -> Any:
        return _bounded_json_answer(value)


class CourseExerciseHintRequest(StrictCourseRequest):
    snapshot_token: Sha256
    idempotency_key: StableKey
    chapter_key: StableKey
    concept_key: StableKey
    attempt_key: StableKey
    hint_index: int = Field(ge=1, le=4)


class CourseExerciseRevealRequest(StrictCourseRequest):
    snapshot_token: Sha256
    idempotency_key: StableKey
    chapter_key: StableKey
    concept_key: StableKey
    attempt_key: StableKey


class CourseExerciseVerificationRequest(StrictCourseRequest):
    """Bind a human approval to the exact current exercise answer snapshot."""

    snapshot_token: Sha256
    expected_answer_confirmation: Any
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("expected_answer_confirmation")
    @classmethod
    def confirmation_is_bounded_json(cls, value: Any) -> Any:
        return _bounded_json_answer(value)

    @field_validator("reason")
    @classmethod
    def reason_is_not_blank(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("verification reason must not be blank")
        return clean


class CourseTransferGradeRequest(StrictCourseRequest):
    snapshot_token: Sha256
    chapter_key: StableKey
    concept_key: StableKey
    source_attempt_key: StableKey
    attempt_key: StableKey
    transfer_task_key: StableKey
    answer: Any

    _bounded_answer = field_validator("answer")(_bounded_json_answer)


class CourseLearningEventResponse(BaseModel):
    event: LearningEvent
    mastery: ConceptMastery | None = None


class CourseExerciseGradeResponse(BaseModel):
    grade: GradeResult
    mastery: ConceptMastery | None = None
    event_key: StableKey | None = None
    snapshot_token: Sha256


class CourseAnswerFormat(BaseModel):
    """Input shape metadata with no expected value or grading oracle."""

    model_config = ConfigDict(extra="forbid")

    kind: AnswerType
    component_count: int | None = Field(default=None, ge=1, le=20)
    unit_required: bool = False
    parts: tuple["CourseAnswerFormat", ...] = Field(
        default_factory=tuple, max_length=20
    )

    @model_validator(mode="after")
    def shape_matches_kind(self) -> "CourseAnswerFormat":
        if self.kind == "vector" and self.component_count is None:
            raise ValueError("vector answer format requires component_count")
        if self.kind != "vector" and self.component_count is not None:
            raise ValueError("only vector formats declare component_count")
        if self.kind == "multipart" and not self.parts:
            raise ValueError("multipart answer format requires parts")
        if self.kind != "multipart" and self.parts:
            raise ValueError("only multipart formats declare parts")
        if self.kind == "unit" and not self.unit_required:
            raise ValueError("unit answer format requires a unit")
        if self.kind not in {"unit", "vector"} and self.unit_required:
            raise ValueError("this answer format does not accept a unit")
        return self


class CourseTransferTaskResponse(BaseModel):
    """Learner-safe transfer task with its deterministic grader withheld."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    key: StableKey
    prompt: str
    invariant_concept_keys: tuple[StableKey, ...]
    dimensions: tuple[TransferDimension, ...]
    answer_type: AnswerType
    answer_format: CourseAnswerFormat
    difficulty: DifficultyVector
    anchor_ids: tuple[str, ...]


class ExerciseVerificationResponse(ExerciseVerification):
    """Public exercise verification provenance shared by Build and Learn."""


class CourseExerciseResponse(BaseModel):
    """Learner-safe exercise projection with every grading oracle removed."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    key: StableKey
    chapter_key: StableKey
    prompt: str
    concept_keys: tuple[StableKey, ...]
    exercise_type: Literal[
        "worked_source",
        "source_practice",
        "generated_core",
        "generated_challenge",
        "transfer",
    ]
    answer_type: AnswerType
    answer_format: CourseAnswerFormat
    snapshot_token: Sha256
    source_anchor_ids: tuple[str, ...]
    source_number: str | None = None
    source_section: str | None = None
    difficulty: DifficultyVector
    is_core: bool
    is_gating: bool
    is_source_level: bool
    verification: ExerciseVerificationResponse
    learning_blocked_reason: Literal["verification_required"] | None = None
    transfer: CourseTransferTaskResponse | None = None


class CourseExerciseHintResponse(BaseModel):
    snapshot_token: Sha256
    hint_index: int = Field(ge=1, le=4)
    total_hints: int = Field(ge=1, le=4)
    hint: str = Field(min_length=1, max_length=2000)
    event: LearningEvent
    mastery: ConceptMastery | None = None


class CourseExerciseRevealResponse(BaseModel):
    snapshot_token: Sha256
    answer: Any
    transfer: CourseTransferTaskResponse | None = None
    events: tuple[LearningEvent, ...] = Field(min_length=1, max_length=2)
    mastery: ConceptMastery | None = None


class CourseLearnerChapterSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_key: StableKey
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1, max_length=100_000)
    anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    provenance: ProvenanceLabel


class CourseLearnerFormula(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: StableKey
    latex: str = Field(min_length=1, max_length=4000)
    meaning: str = Field(min_length=1, max_length=2000)
    anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    unit_expression: str | None = Field(default=None, max_length=500)
    provenance: ProvenanceLabel


class CourseLearnerWorkedExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: StableKey
    prompt: str = Field(min_length=1, max_length=4000)
    steps: tuple[str, ...] = Field(min_length=1, max_length=50)
    answer: str = Field(min_length=1, max_length=4000)
    anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    unit_expression: str | None = Field(default=None, max_length=500)
    provenance: ProvenanceLabel


class CourseLearnerChapterArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=4000)
    prerequisites: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    objectives: tuple[str, ...] = Field(min_length=1, max_length=100)
    sections: tuple[CourseLearnerChapterSection, ...] = Field(
        min_length=1, max_length=100
    )
    definitions: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    formulas: tuple[CourseLearnerFormula, ...] = Field(
        default_factory=tuple, max_length=100
    )
    worked_examples: tuple[CourseLearnerWorkedExample, ...] = Field(
        default_factory=tuple, max_length=100
    )
    misconceptions: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    pitfalls: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    quick_reference: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    citations: tuple[str, ...] = Field(default_factory=tuple, max_length=500)


class CourseLearnerChapterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_id: str = Field(pattern=r"^course:[^:]+$")
    course_version_id: str = Field(pattern=r"^course_version:[^:]+$")
    chapter_key: StableKey
    chapter_no: int = Field(ge=1)
    title: str = Field(min_length=1)
    status: Literal["published"]
    snapshot_token: Sha256
    artifact: CourseLearnerChapterArtifact


class CourseLearnerSourceResponse(BaseModel):
    """Current-chapter evidence metadata without Source record IDs or paths."""

    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(min_length=1, max_length=300)
    filename: str = Field(min_length=1, max_length=500)
    kind: Literal["pdf_page", "pptx_slide"]
    index: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)
    source_role: Literal["PRIMARY", "SUPPLEMENT"]
    bbox: tuple[float, float, float, float] | None = None


class CourseLearnerSourcesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_token: Sha256
    sources: tuple[CourseLearnerSourceResponse, ...]


class CourseLearnerNoteResponse(BaseModel):
    """A note bound to one exact published chapter record."""

    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(pattern=r"^course_note:[^:]+$")
    block_key: StableKey
    content: str = Field(min_length=1, max_length=20_000)
    orphan_status: Literal["active", "orphaned"]
    created: datetime | None = None


class CourseLearnerNotesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_token: Sha256
    notes: tuple[CourseLearnerNoteResponse, ...]


class CourseLearnerNoteCreateRequest(StrictCourseRequest):
    snapshot_token: Sha256
    block_key: StableKey
    content: str = Field(min_length=1, max_length=20_000)


class CourseTutorSessionCreateRequest(StrictCourseRequest):
    snapshot_token: Sha256
    chapter_key: StableKey
    model: ModelSelection


class CourseTutorSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(pattern=r"^course_tutor_session:[^:]+$")
    course_version_id: str = Field(pattern=r"^course_version:[^:]+$")
    chapter_key: StableKey
    model: ModelSelection
    status: Literal["active", "closed", "stale"]
    turns: tuple[TutorTurn, ...] = Field(default_factory=tuple, max_length=2000)
    created: datetime | None = None


class CourseTutorMessageRequest(StrictCourseRequest):
    snapshot_token: Sha256
    idempotency_key: StableKey
    content: str = Field(min_length=1, max_length=20_000)
    intent: Literal["explain", "diagnose", "hint", "reveal"]
    exercise_key: StableKey | None = None
    concept_key: StableKey | None = None
    attempt_key: StableKey | None = None

    @model_validator(mode="after")
    def reveal_scope_is_explicit(self) -> "CourseTutorMessageRequest":
        scoped_values = (
            self.exercise_key,
            self.concept_key,
            self.attempt_key,
        )
        if self.intent in {"diagnose", "hint", "reveal"} and any(
            value is None for value in scoped_values
        ):
            raise ValueError(
                "diagnose, hint, and reveal require exercise_key, concept_key, and attempt_key"
            )
        if self.intent == "explain" and any(
            value is not None for value in scoped_values
        ):
            raise ValueError(
                "exercise and attempt identities are not accepted for explain"
            )
        return self


class CourseTutorMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_token: Sha256
    response: TutorResponse


class CourseDraftOperationRequest(StrictCourseRequest):
    revision_token: Sha256
    operation: DraftOperation


class CourseDraftValidateRequest(StrictCourseRequest):
    revision_token: Sha256


class CourseDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_key: StableKey
    chapter_status: str = Field(min_length=1, max_length=50)
    editable: bool
    revision_no: int = Field(ge=0)
    revision_token: Sha256
    revision_status: Literal["draft", "validated"] | None = None
    artifact_hash: Sha256
    artifact: ChapterArtifact
    exercises: tuple[ExerciseBlueprint, ...] = Field(default_factory=tuple, max_length=500)


class CourseDraftValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: CourseDraftResponse
    valid: bool
    checked: tuple[ValidationCheck, ...] = Field(max_length=6)
    findings: tuple[ValidationFinding, ...] = Field(default_factory=tuple, max_length=500)


class CourseExportCreateRequest(StrictCourseRequest):
    include_originals: bool = False


class CourseExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: str = Field(pattern=r"^course_export:[^:]+$")
    course_id: str = Field(pattern=r"^course:[^:]+$")
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    download_ready: bool
    manifest: CourseBundleManifest | None = None
    error_message: str | None = None


class CourseBundleImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_id: str = Field(pattern=r"^course:[^:]+$")
    course_title: str = Field(min_length=1, max_length=300)
    record_counts: Dict[StableKey, int]

    @field_validator("record_counts")
    @classmethod
    def counts_are_bounded(cls, value: Dict[str, int]) -> Dict[str, int]:
        if len(value) > 100 or any(count < 0 for count in value.values()):
            raise ValueError("record counts are invalid")
        return value


class CourseLearningChapterOverview(BaseModel):
    chapter_key: StableKey
    chapter_no: int = Field(ge=1)
    title: str
    snapshot_token: Sha256
    latest_position: LearningEvent | None = None


class CourseConceptResponse(BaseModel):
    key: StableKey
    label: str = Field(min_length=1, max_length=300)


class CourseLearningOverviewResponse(BaseModel):
    course_id: str
    course_version_id: str
    chapters: tuple[CourseLearningChapterOverview, ...]
    concepts: tuple[CourseConceptResponse, ...] = ()
    masteries: tuple[ConceptMastery, ...]
    review_queue: tuple[ReviewQueueItem, ...]


class CourseJobRequest(StrictCourseRequest):
    prompt_version: str = Field("v1", min_length=1, max_length=100)
    force: bool = False


class CourseAnchoredJobRequest(CourseJobRequest):
    anchor_ids: List[str] = Field(..., min_length=1, max_length=500)
    model: ModelSelection

    @field_validator("anchor_ids")
    @classmethod
    def anchors_are_unique(cls, value: List[str]) -> List[str]:
        if any(not anchor_id.strip() for anchor_id in value):
            raise ValueError("anchor IDs must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("anchor IDs must be unique")
        return value


class CourseEvidenceBuildRequest(StrictCourseRequest):
    source_id: str = Field(..., min_length=1)
    role: Literal["PRIMARY", "SUPPLEMENT"]
    force: bool = False


class CourseOutlineGenerateRequest(CourseAnchoredJobRequest):
    available_lab_keys: List[SafeLabKey] = Field(..., min_length=1, max_length=100)

    @field_validator("available_lab_keys")
    @classmethod
    def lab_keys_are_unique(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("lab keys must be unique")
        return value


class CourseChapterGenerateRequest(CourseAnchoredJobRequest):
    pass


class CourseChapterReviewRequest(CourseAnchoredJobRequest):
    escalation_model: ModelSelection


class CourseExerciseBankGenerateRequest(CourseAnchoredJobRequest):
    prompt_version: str = Field("v2", min_length=1, max_length=100)
    review_model: ModelSelection


class CourseExerciseBuildStatusResponse(BaseModel):
    run_id: str | None = None
    command_id: str | None = None
    status: Literal[
        "not_started",
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ]
    error_message: str | None = None
    exercise_count: int = Field(default=0, ge=0)


class CourseRetrievalRequest(StrictCourseRequest):
    anchor_ids: List[str] = Field(..., min_length=1, max_length=500)

    @field_validator("anchor_ids")
    @classmethod
    def anchors_are_unique(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("anchor IDs must be unique")
        return value


class CourseFindingUpdate(StrictCourseRequest):
    status: Literal["resolved", "acknowledged"]
    resolution_reason: str = Field(..., min_length=1, max_length=2000)


class CourseJobResponse(BaseModel):
    command_id: str
    run_id: str
    status: str

class CourseCreate(StrictCourseRequest):
    title: str = Field(..., min_length=1, description="Course title")
    subject: Optional[Literal["math", "physics"]] = Field(
        None, description="Open Course subject"
    )
    description: Optional[str] = Field(None, description="Course description")
    language: str = Field("zh-CN", min_length=2, max_length=20)
    notebook_id: Optional[str] = Field(
        None, description="Validated existing notebook; omitted creates one"
    )
    config: Optional[Dict[str, Any]] = Field(
        None, description="Generation config (models, locale, validation)"
    )


class CourseUpdate(StrictCourseRequest):
    title: Optional[str] = Field(None, min_length=1, description="Course title")
    subject: Optional[Literal["math", "physics"]] = Field(
        None, description="Open Course subject"
    )
    description: Optional[str] = Field(None, description="Course description")
    language: Optional[str] = Field(None, min_length=2, max_length=20)
    config: Optional[Dict[str, Any]] = Field(
        None, description="Generation config"
    )


class CourseOutlineUpdate(BaseModel):
    outline: Dict[str, Any] = Field(
        ..., description="Approved outline: chapters list + optional dependency_graph"
    )


class CourseOutlineApproval(BaseModel):
    version_id: str = Field(..., min_length=1)
    confirmation: str


class CourseSourceAssociation(BaseModel):
    source_id: str = Field(..., min_length=1)
    role: Literal["PRIMARY", "SUPPLEMENT"]


class CourseStatusUpdate(BaseModel):
    status: str = Field(..., description="Target course status")


class CourseVersionCreate(BaseModel):
    outline_hash: Optional[str] = Field(
        None, description="Hash of the approved outline this version is based on"
    )
    outline_artifact: Optional[Dict[str, Any]] = None
    input_hash: Optional[str] = None


class CourseVersionStatusUpdate(BaseModel):
    status: str = Field(..., description="Target version status")


class ChapterCreate(BaseModel):
    chapter_no: int = Field(..., ge=1, description="Chapter number (1-based)")
    title: str = Field(..., min_length=1, description="Chapter title")
    chapter_key: Optional[str] = Field(None, min_length=1, max_length=100)
    artifact: Optional[Dict[str, Any]] = None
    input_hash: Optional[str] = None


class ChapterUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, description="Chapter title")
    content: Optional[str] = Field(None, description="Chapter content")
    citations: Optional[List[Dict[str, Any]]] = Field(
        None, description="Citation anchors into evidence"
    )
    review_status: Optional[str] = Field(None, description="Target review status")
    validation_status: Optional[str] = Field(
        None, description="Target validation status"
    )
    status: Optional[str] = Field(None, description="Target chapter lifecycle status")
    artifact: Optional[Dict[str, Any]] = None
    input_hash: Optional[str] = None


class LabCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lab_type: Literal[
        "function_plot", "parametric_curve", "vector_field", "geometry", "kinematics"
    ]
    chapter: Optional[str] = Field(None, description="Optional chapter record id")
    prompt: Optional[str] = Field(None, max_length=4000, description="Exercise prompt")
    payload: LabSpecVariant = Field(..., description="Bounded declarative lab payload")
    answer: Optional[Dict[str, Any]] = Field(
        None, description="Expected answer / check specification"
    )

    @model_validator(mode="after")
    def payload_kind_matches_lab_type(self):
        if self.payload.kind != self.lab_type:
            raise ValueError("lab_type must match payload.kind")
        return self


class AttemptCreate(BaseModel):
    answers: Dict[str, Any] = Field(..., description="Student answers payload")
    chapter_key: Optional[str] = None
    exercise_key: Optional[str] = None


class CourseChapterAttemptCreate(StrictCourseRequest):
    answers: Dict[str, Any] = Field(..., description="Student answers payload")
    exercise_key: Optional[str] = Field(None, min_length=1, max_length=100)
    answer: Optional[str] = Field(None, max_length=10000)
    hints_used: Optional[int] = Field(None, ge=0, le=5)
    answer_revealed: Optional[bool] = None
    transfer_completed: Optional[bool] = None


class AttemptStatusUpdate(BaseModel):
    status: str = Field(..., description="Target attempt status")


class ProgressUpdate(StrictCourseRequest):
    chapter_key: Optional[str] = Field(None, min_length=1, max_length=100)
    block_key: Optional[str] = Field(None, min_length=1, max_length=200)
    status: str = Field(..., description="Target progress status")

    @model_validator(mode="after")
    def block_requires_chapter(self) -> "ProgressUpdate":
        if self.block_key is not None and self.chapter_key is None:
            raise ValueError("block_key requires chapter_key")
        return self


class CourseNoteCreate(StrictCourseRequest):
    chapter_key: Optional[str] = Field(None, min_length=1, max_length=100)
    block_key: Optional[str] = Field(None, min_length=1, max_length=200)
    content: str = Field(..., min_length=1, description="Note content")

    @model_validator(mode="after")
    def block_requires_chapter(self) -> "CourseNoteCreate":
        if self.block_key is not None and self.chapter_key is None:
            raise ValueError("block_key requires chapter_key")
        return self


class CourseNoteReattach(StrictCourseRequest):
    chapter_key: str = Field(..., min_length=1, max_length=100)
    block_key: str = Field(..., min_length=1, max_length=200)


class ChapterPublish(BaseModel):
    course_id: str = Field(..., min_length=1)
