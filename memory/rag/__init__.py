"""Local, privacy-preserving retrieval components for personal memory."""

from memory.rag.personal import (
    DEFAULT_RAG_DATABASE_PATH,
    HashEmbeddingProvider,
    MemoryDocument,
    OpenAIEmbeddingConfigurationError,
    OpenAIEmbeddingProvider,
    PersonalRagStore,
    RetrievedMemory,
    configured_embedding_provider,
)
from memory.rag.consolidator import (
    ConsolidationPreview,
    ConsolidatedMemory,
    KimiMemorySummarizer,
    MemoryConsolidationError,
    MemorySummarizer,
)

__all__ = [
    "DEFAULT_RAG_DATABASE_PATH",
    "HashEmbeddingProvider",
    "MemoryDocument",
    "OpenAIEmbeddingConfigurationError",
    "OpenAIEmbeddingProvider",
    "PersonalRagStore",
    "RetrievedMemory",
    "configured_embedding_provider",
    "ConsolidationPreview",
    "ConsolidatedMemory",
    "KimiMemorySummarizer",
    "MemoryConsolidationError",
    "MemorySummarizer",
]
