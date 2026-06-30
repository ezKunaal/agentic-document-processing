"""
Pipeline Orchestrator

The single entry point for processing a document.
Chains: Classifier → Extractor → Validator → Router

Escalation logic lives HERE (not in individual agents).
Each agent has one job. The orchestrator decides what happens
when things go wrong — escalate, retry, or fail gracefully.

This is the piece you'd connect to:
  - Azure Functions (event-driven trigger from Service Bus)
  - A background worker loop
  - A direct API call (for testing)
"""
import io
import json
from typing import Optional

from config.models import (
    Document,
    EscalationReason,
    ProcessingStatus,
)
from config.settings import settings
from agents.classifier import ClassifierAgent
from agents.extractor import ExtractorAgent
from agents.validator import ValidatorAgent
from agents.router import RouterAgent
from hitl.review_queue import ReviewQueue
from observability.logger import log_info, log_error, persist_audit, trace_step


class DocumentPipeline:
    """
    Orchestrates the full document processing pipeline.
    Instantiate once and call .process(doc) for each document.
    """

    def __init__(self) -> None:
        self.classifier = ClassifierAgent()
        self.extractor = ExtractorAgent()
        self.validator = ValidatorAgent()
        self.router = RouterAgent()
        self.review_queue = ReviewQueue()

    def process(self, doc: Document) -> Document:
        """
        Main entry point. Returns the document in its final state.
        The caller inspects doc.status to know the outcome.
        """
        log_info("pipeline.start", doc.id, filename=doc.filename)

        try:
            with trace_step("pipeline", "full_run", doc.id):

                # Step 1 — Classify
                doc = self.classifier.run(doc)
                if self._should_escalate_confidence(doc):
                    return self._escalate(
                        doc,
                        EscalationReason.LOW_CONFIDENCE,
                        f"Confidence {doc.confidence:.2f} below threshold {settings.confidence_threshold}",
                    )

                # Step 2 — Extract
                doc = self.extractor.run(doc)

                # Step 3 — Validate
                doc = self.validator.run(doc)
                if not doc.validation_passed:
                    # Some validation failures are hard-stops (missing required fields)
                    # Others are soft warnings. Missing required fields → escalate.
                    if self._has_missing_required_fields(doc):
                        return self._escalate(
                            doc,
                            EscalationReason.MISSING_REQUIRED_FIELDS,
                            f"Validation errors: {doc.validation_errors}",
                        )
                    # High-value invoice already flagged by validator
                    if doc.escalation_reason == EscalationReason.AMOUNT_EXCEEDS_THRESHOLD:
                        return self._escalate(
                            doc,
                            EscalationReason.AMOUNT_EXCEEDS_THRESHOLD,
                            doc.validation_errors[0] if doc.validation_errors else "",
                        )

                # Step 4 — Route
                doc = self.router.run(doc)

        except Exception as exc:
            log_error("pipeline.unhandled_exception", doc.id, error=str(exc))
            doc.status = ProcessingStatus.FAILED
            doc.add_audit("pipeline", "fatal_error", str(exc))

        finally:
            persist_audit(doc.id, doc.to_dict())
            log_info("pipeline.complete", doc.id, status=doc.status.value)

        return doc

    def _should_escalate_confidence(self, doc: Document) -> bool:
        return doc.confidence < settings.confidence_threshold

    def _has_missing_required_fields(self, doc: Document) -> bool:
        return any("Required field missing" in e for e in doc.validation_errors)

    def _escalate(
        self,
        doc: Document,
        reason: EscalationReason,
        note: str,
    ) -> Document:
        doc.status = ProcessingStatus.ESCALATED
        doc.escalation_reason = reason
        doc.escalation_note = note
        doc.add_audit("pipeline", "escalated", f"reason={reason.value} note={note}")

        # Push to human review queue
        self.review_queue.enqueue(doc)

        log_info(
            "pipeline.escalated",
            doc.id,
            reason=reason.value,
            note=note,
        )
        return doc


# ── Helper: build a Document from raw bytes ───────────────────────────────────

def build_document_from_upload(
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> Document:
    """
    Converts raw upload bytes into a Document with extracted text content.
    Supports PDF, plain text, and email (.eml) formats.
    """
    doc = Document(
        filename=filename,
        content_type=content_type,
        raw_content=content,
    )

    # Text extraction — in production use Azure Doc Intelligence for PDFs
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        doc.text_content = _extract_pdf_text(content)
    elif content_type in ("text/plain", "message/rfc822") or filename.lower().endswith((".txt", ".eml")):
        doc.text_content = content.decode("utf-8", errors="replace")
    else:
        # Best effort — try UTF-8 decode
        try:
            doc.text_content = content.decode("utf-8", errors="replace")
        except Exception:
            doc.text_content = ""

    return doc


def _extract_pdf_text(content: bytes) -> str:
    """
    Extract text from PDF bytes.
    Uses pypdf locally; Azure Document Intelligence in production.
    """
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    except ImportError:
        return "[PDF text extraction requires pypdf — install with: pip install pypdf]"
    except Exception as exc:
        return f"[PDF extraction failed: {exc}]"
