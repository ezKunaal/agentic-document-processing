"""
Human-in-the-Loop Review Queue

Stores documents that need human review.
In production: backed by Azure Service Bus (separate "review" queue)
or Azure Table Storage with SLA timestamps.

Provides:
  - enqueue(doc): add to review queue
  - list_pending(): all documents awaiting review
  - get(doc_id): single document for review UI
  - resolve(doc_id, corrections): human submits correction → re-inject into pipeline
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config.models import Document, DocumentType, ProcessingStatus
from observability.logger import log_info, log_warning

# SLA: escalated documents must be reviewed within this many hours
REVIEW_SLA_HOURS = 4


class ReviewQueue:
    """
    Thread-safe in-memory review queue.
    Swap _store for Azure Service Bus / Table Storage in production.
    """

    _store: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    def enqueue(self, doc: Document) -> None:
        with self._lock:
            self._store[doc.id] = {
                "doc": doc,
                "queued_at": datetime.utcnow(),
                "sla_deadline": datetime.utcnow() + timedelta(hours=REVIEW_SLA_HOURS),
                "resolved": False,
                "resolver": None,
                "resolved_at": None,
            }
        log_info(
            "hitl.enqueued",
            doc.id,
            reason=doc.escalation_reason.value if doc.escalation_reason else "unknown",
            sla_hours=REVIEW_SLA_HOURS,
        )

    def list_pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            now = datetime.utcnow()
            result = []
            for entry in self._store.values():
                if not entry["resolved"]:
                    doc: Document = entry["doc"]
                    result.append({
                        "doc_id": doc.id,
                        "filename": doc.filename,
                        "doc_type": doc.doc_type.value,
                        "confidence": doc.confidence,
                        "escalation_reason": (
                            doc.escalation_reason.value if doc.escalation_reason else None
                        ),
                        "escalation_note": doc.escalation_note,
                        "extracted_fields": doc.extracted_fields,
                        "validation_errors": doc.validation_errors,
                        "queued_at": entry["queued_at"].isoformat(),
                        "sla_deadline": entry["sla_deadline"].isoformat(),
                        "sla_breached": now > entry["sla_deadline"],
                    })
            return result

    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._store.get(doc_id)
            if not entry:
                return None
            doc: Document = entry["doc"]
            return {
                "doc_id": doc.id,
                "filename": doc.filename,
                "content_type": doc.content_type,
                "doc_type": doc.doc_type.value,
                "confidence": doc.confidence,
                "escalation_reason": (
                    doc.escalation_reason.value if doc.escalation_reason else None
                ),
                "escalation_note": doc.escalation_note,
                "extracted_fields": doc.extracted_fields,
                "validation_errors": doc.validation_errors,
                "audit_trail": [a.to_dict() for a in doc.audit_trail],
                "text_snippet": doc.text_content[:500] if doc.text_content else "",
                "queued_at": entry["queued_at"].isoformat(),
                "sla_deadline": entry["sla_deadline"].isoformat(),
                "resolved": entry["resolved"],
            }

    def resolve(
        self,
        doc_id: str,
        corrections: Dict[str, Any],
        reviewer: str = "human",
    ) -> Optional[Document]:
        """
        Human submits their corrections.
        Returns the corrected document ready for re-injection into the pipeline.
        """
        with self._lock:
            entry = self._store.get(doc_id)
            if not entry:
                log_warning("hitl.resolve.not_found", doc_id)
                return None
            if entry["resolved"]:
                log_warning("hitl.resolve.already_resolved", doc_id)
                return None

            doc: Document = entry["doc"]

            # Apply corrections
            if "doc_type" in corrections:
                doc.doc_type = DocumentType(corrections["doc_type"])
                doc.confidence = 1.0  # human override → full confidence

            if "extracted_fields" in corrections:
                doc.extracted_fields.update(corrections["extracted_fields"])

            # Mark reviewed
            doc.status = ProcessingStatus.HUMAN_REVIEWED
            doc.add_audit(
                "human",
                "reviewed",
                f"reviewer={reviewer} corrections={list(corrections.keys())}",
            )

            entry["resolved"] = True
            entry["resolver"] = reviewer
            entry["resolved_at"] = datetime.utcnow()

        log_info(
            "hitl.resolved",
            doc_id,
            reviewer=reviewer,
            corrections=list(corrections.keys()),
        )
        return doc
