"""Infrastructure adapters for TicketPilot.

The default Jira adapter is deliberately disabled.  ``LocalDemoGateway`` is an
explicitly synthetic, offline alternative for developing and testing the UI.
"""

from .adapters import SQLiteAuditSink, SQLiteTicketRepository
from .config import (
    DEFAULT_CACHE_TTL_HOURS,
    DEFAULT_PROJECT,
    AppConfig,
    ConfigError,
    load_config,
)
from .credentials import (
    CredentialError,
    CredentialStore,
    CredentialStoreUnavailable,
    KeyringCredentialStore,
    MemoryCredentialStore,
    WindowsCredentialStore,
    create_credential_store,
)
from .gateways import (
    DisabledJiraGateway,
    GatewayDisabledError,
    GatewayError,
    GatewayNotFoundError,
    GatewayValidationError,
    JiraGatewayAdapter,
    LocalDemoGateway,
    MetadataGatewayAdapter,
)
from .metadata_cache import (
    DEFAULT_SMALL_METADATA_KINDS,
    CachingMetadataProvider,
    MetadataRefreshResult,
)
from .persistence import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_CACHE_TTL,
    CacheStatus,
    MetadataCacheEntry,
    PersistenceError,
    SQLiteStore,
)
from .security import (
    REDACTED,
    TRUNCATED,
    RedactingFilter,
    SafeJsonFormatter,
    SensitiveDataError,
    assert_no_secret_fields,
    configure_secure_file_logging,
    is_sensitive_key,
    redact_text,
    register_secret,
    safe_error_message,
    safe_json_dumps,
    sanitize,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_CACHE_TTL",
    "DEFAULT_CACHE_TTL_HOURS",
    "DEFAULT_PROJECT",
    "DEFAULT_SMALL_METADATA_KINDS",
    "REDACTED",
    "TRUNCATED",
    "AppConfig",
    "CacheStatus",
    "CachingMetadataProvider",
    "ConfigError",
    "CredentialError",
    "CredentialStore",
    "CredentialStoreUnavailable",
    "DisabledJiraGateway",
    "GatewayDisabledError",
    "GatewayError",
    "GatewayNotFoundError",
    "GatewayValidationError",
    "JiraGatewayAdapter",
    "KeyringCredentialStore",
    "LocalDemoGateway",
    "MemoryCredentialStore",
    "MetadataCacheEntry",
    "MetadataGatewayAdapter",
    "MetadataRefreshResult",
    "PersistenceError",
    "RedactingFilter",
    "SQLiteAuditSink",
    "SQLiteStore",
    "SQLiteTicketRepository",
    "SafeJsonFormatter",
    "SensitiveDataError",
    "WindowsCredentialStore",
    "assert_no_secret_fields",
    "configure_secure_file_logging",
    "create_credential_store",
    "is_sensitive_key",
    "load_config",
    "redact_text",
    "register_secret",
    "safe_error_message",
    "safe_json_dumps",
    "sanitize",
]
