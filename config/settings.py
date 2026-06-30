"""
Central settings — loaded from environment variables.
All Azure credentials live in Key Vault in production;
for local dev we use a .env file (never committed to git).
"""
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # Azure Storage
    azure_storage_connection_string: str = field(
        default_factory=lambda: os.getenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
    )
    azure_storage_container: str = field(
        default_factory=lambda: os.getenv("AZURE_STORAGE_CONTAINER", "documents")
    )

    # Azure Service Bus
    service_bus_connection_string: str = field(
        default_factory=lambda: os.getenv("SERVICE_BUS_CONNECTION_STRING", "")
    )
    service_bus_queue: str = field(
        default_factory=lambda: os.getenv("SERVICE_BUS_QUEUE", "document-processing")
    )

    # Azure OpenAI
    azure_openai_endpoint: str = field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT", "")
    )
    azure_openai_key: str = field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_KEY", "")
    )
    azure_openai_deployment: str = field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    )

    # Azure Document Intelligence (Form Recognizer)
    doc_intelligence_endpoint: str = field(
        default_factory=lambda: os.getenv("DOC_INTELLIGENCE_ENDPOINT", "")
    )
    doc_intelligence_key: str = field(
        default_factory=lambda: os.getenv("DOC_INTELLIGENCE_KEY", "")
    )

    # Confidence threshold — below this, escalate to human
    confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))
    )

    # App Insights
    app_insights_connection_string: str = field(
        default_factory=lambda: os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    )

    # Local dev mode — uses stubs instead of real Azure services
    local_dev: bool = field(
        default_factory=lambda: os.getenv("LOCAL_DEV", "true").lower() == "true"
    )


settings = Settings()
