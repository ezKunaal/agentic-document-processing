"""
Domain models — the shared language of the entire system.
Every layer speaks these types; nothing else crosses layer boundaries.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


# ─────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────

class DocumentType(str, Enum):
    INVOICE = "invoice"
    CONTRACT = "contract"
    EMAIL = "email"
    COMPLIANCE = "compliance"
    UNKNOWN = "unknown"


class ProcessingStatus(str, Enum):
    RECEIVED = "received"
    CLASSIFYING = "classifying"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    ROUTING = "routing"
    ROUTED = "routed"
    ESCALATED = "escalated"           # Sent to human review
    HUMAN_REVIEWED = "human_reviewed" # Human corrected and re-injected
    FAILED = "failed"


class EscalationReason(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    VALIDATION_FAILED = "validation_failed"
    UNKNOWN_DOC_TYPE = "unknown_doc_type"
    MISSING_REQUIRED_FIELDS = "missing_required_fields"
    AMOUNT_EXCEEDS_THRESHOLD = "amount_exceeds_threshold"


class DownstreamSystem(str, Enum):
    FINANCE = "finance"
    CRM = "crm"
    COMPLIANCE = "compliance"
    UNROUTABLE = "unroutable"


# ─────────────────────────────────────────────
# Core Document Model
# ─────────────────────────────────────────────

@dataclass
class Document:
    """
    The central unit of work that flows through the entire pipeline.
    Every agent reads from and writes back to this object.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    filename: str = ""
    content_type: str = ""          # MIME type
    raw_content: bytes = b""        # Original file bytes
    text_content: str = ""          # Extracted plain text

    # Set by Classifier Agent
    doc_type: DocumentType = DocumentType.UNKNOWN
    confidence: float = 0.0

    # Set by Extractor Agent
    extracted_fields: Dict[str, Any] = field(default_factory=dict)

    # Set by Validator Agent
    validation_passed: bool = False
    validation_errors: List[str] = field(default_factory=list)

    # Set by Router Agent
    destination: Optional[DownstreamSystem] = None

    # Pipeline tracking
    status: ProcessingStatus = ProcessingStatus.RECEIVED
    escalation_reason: Optional[EscalationReason] = None
    escalation_note: str = ""

    # Audit trail — append-only log of every state transition
    audit_trail: List[AuditEntry] = field(default_factory=list)

    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def add_audit(self, agent: str, action: str, detail: str = "") -> None:
        self.audit_trail.append(
            AuditEntry(
                agent=agent,
                action=action,
                detail=detail,
                timestamp=datetime.utcnow(),
                status=self.status,
            )
        )
        self.updated_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "content_type": self.content_type,
            "doc_type": self.doc_type.value,
            "confidence": self.confidence,
            "extracted_fields": self.extracted_fields,
            "validation_passed": self.validation_passed,
            "validation_errors": self.validation_errors,
            "destination": self.destination.value if self.destination else None,
            "status": self.status.value,
            "escalation_reason": self.escalation_reason.value if self.escalation_reason else None,
            "escalation_note": self.escalation_note,
            "audit_trail": [a.to_dict() for a in self.audit_trail],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class AuditEntry:
    agent: str
    action: str
    status: ProcessingStatus
    detail: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "status": self.status.value,
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat(),
        }


# ─────────────────────────────────────────────
# Routing Rule Model (config-driven extensibility)
# ─────────────────────────────────────────────

@dataclass
class RoutingRule:
    """
    A single routing rule.  New rules = new entries in routing_rules.json.
    No code changes required to add new document types or destinations.
    """
    doc_type: DocumentType
    destination: DownstreamSystem
    conditions: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
