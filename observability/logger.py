"""
Observability — structured logging, metrics, and audit trail.

In production this ships to:
  - Azure Monitor / App Insights (via OpenTelemetry)
  - Cosmos DB (audit trail, queryable forever)

Locally it writes structured JSON to stdout so docker-compose logs are readable.
"""
import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Optional

from config.settings import settings

# ── configure root logger once ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",   # we emit JSON; no extra formatting needed
)
_root = logging.getLogger("doc-agent")


def _emit(level: str, event: str, doc_id: Optional[str] = None, **kwargs: Any) -> None:
    record = {
        "ts": datetime.utcnow().isoformat(),
        "level": level,
        "event": event,
        **({} if doc_id is None else {"doc_id": doc_id}),
        **kwargs,
    }
    msg = json.dumps(record)
    if level == "ERROR":
        _root.error(msg)
    elif level == "WARNING":
        _root.warning(msg)
    else:
        _root.info(msg)

    # In production, also ship to App Insights via OpenTelemetry SDK.
    # Omitted here to avoid requiring Azure credentials for local dev.
    if not settings.local_dev and settings.app_insights_connection_string:
        _ship_to_app_insights(record)


def _ship_to_app_insights(record: Dict[str, Any]) -> None:
    """Placeholder — swap in azure-monitor-opentelemetry-exporter in production."""
    pass


# ── public API ────────────────────────────────────────────────────────────────

def log_info(event: str, doc_id: Optional[str] = None, **kwargs: Any) -> None:
    _emit("INFO", event, doc_id, **kwargs)


def log_warning(event: str, doc_id: Optional[str] = None, **kwargs: Any) -> None:
    _emit("WARNING", event, doc_id, **kwargs)


def log_error(event: str, doc_id: Optional[str] = None, **kwargs: Any) -> None:
    _emit("ERROR", event, doc_id, **kwargs)


@contextmanager
def trace_step(agent: str, step: str, doc_id: Optional[str] = None):
    """
    Context manager that wraps an agent step with timing.

    Usage:
        with trace_step("classifier", "classify_document", doc.id):
            ...do work...

    Emits INFO on entry, INFO+duration on success, ERROR on exception.
    In production, this would create an OpenTelemetry span.
    """
    start = time.perf_counter()
    log_info(f"{agent}.{step}.start", doc_id)
    try:
        yield
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_info(f"{agent}.{step}.complete", doc_id, elapsed_ms=elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_error(
            f"{agent}.{step}.failed",
            doc_id,
            elapsed_ms=elapsed_ms,
            error=str(exc),
        )
        raise


# ── In-memory audit store (swap for Cosmos DB in production) ─────────────────

_audit_store: Dict[str, Any] = {}


def persist_audit(doc_id: str, doc_dict: Dict[str, Any]) -> None:
    """
    Persist the document's audit trail.

    Local dev: in-memory dict (survives the process lifetime).
    Production: upsert into Cosmos DB with doc_id as partition key.
    """
    _audit_store[doc_id] = {
        "persisted_at": datetime.utcnow().isoformat(),
        **doc_dict,
    }
    log_info("audit.persisted", doc_id, status=doc_dict.get("status"))


def get_audit(doc_id: str) -> Optional[Dict[str, Any]]:
    return _audit_store.get(doc_id)


def list_audits() -> Dict[str, Any]:
    return dict(_audit_store)
