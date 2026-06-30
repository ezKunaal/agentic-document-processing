"""
Classifier Agent

Responsibility: given a raw document, return (DocumentType, confidence_score).

Real Azure path:
  → Azure OpenAI (GPT-4o) via azure-openai SDK
  → Returns structured JSON: {"doc_type": "invoice", "confidence": 0.95, "reasoning": "..."}

Local dev path:
  → Keyword heuristic on the text content (no Azure credentials needed)

The confidence score is the critical gate for the entire pipeline.
Anything below settings.confidence_threshold is escalated to a human.
"""
import json
import re
from typing import Tuple

from config.models import Document, DocumentType, EscalationReason, ProcessingStatus
from config.settings import settings
from observability.logger import log_info, log_warning, trace_step


CLASSIFIER_SYSTEM_PROMPT = """
You are a document classification agent. Analyse the provided document text and return ONLY valid JSON.

Return format:
{
  "doc_type": "<invoice|contract|email|compliance|unknown>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence explaining the classification>"
}

Classification guide:
- invoice: purchase orders, vendor bills, payment requests, receipts
- contract: agreements, SOWs, NDAs, service contracts, legal documents
- email: correspondence, messages, notifications
- compliance: regulatory filings, audit reports, GDPR/SOX documents
- unknown: anything else

Be conservative with confidence. If genuinely unsure, set confidence below 0.75.
"""


class ClassifierAgent:
    """
    Classifies a document and sets doc.doc_type and doc.confidence.
    Uses Azure OpenAI in production, keyword heuristics in local dev.
    """

    def run(self, doc: Document) -> Document:
        with trace_step("classifier", "classify", doc.id):
            doc.status = ProcessingStatus.CLASSIFYING
            doc.add_audit("classifier", "started")

            if settings.local_dev:
                doc_type, confidence, reasoning = self._classify_local(doc)
            else:
                doc_type, confidence, reasoning = self._classify_openai(doc)

            doc.doc_type = doc_type
            doc.confidence = confidence
            doc.add_audit(
                "classifier",
                "completed",
                f"type={doc_type.value} confidence={confidence:.2f} reason={reasoning}",
            )

            log_info(
                "classifier.result",
                doc.id,
                doc_type=doc_type.value,
                confidence=confidence,
                threshold=settings.confidence_threshold,
            )

            # Flag low-confidence for HITL — the agent itself does NOT escalate;
            # that decision lives in the orchestrator (single responsibility).
            if confidence < settings.confidence_threshold:
                log_warning(
                    "classifier.low_confidence",
                    doc.id,
                    confidence=confidence,
                    threshold=settings.confidence_threshold,
                )

        return doc

    # ── Local dev: keyword heuristic ─────────────────────────────────────────

    def _classify_local(self, doc: Document) -> Tuple[DocumentType, float, str]:
        text = (doc.text_content + " " + doc.filename).lower()

        rules = [
            (DocumentType.INVOICE,    r"\b(invoice|bill|receipt|amount due|vendor|payment|po number)\b", 0.90),
            (DocumentType.CONTRACT,   r"\b(agreement|contract|nda|terms|parties|whereas|signatory|sow)\b", 0.88),
            (DocumentType.EMAIL,      r"\b(from:|to:|subject:|dear|regards|sincerely|@)\b", 0.85),
            (DocumentType.COMPLIANCE, r"\b(compliance|regulation|audit|gdpr|sox|filing|jurisdiction)\b", 0.87),
        ]

        best_type = DocumentType.UNKNOWN
        best_confidence = 0.45  # floor for unknown
        best_reason = "No strong signals found in document text"

        for doc_type, pattern, base_conf in rules:
            matches = re.findall(pattern, text)
            if matches:
                # More keyword hits → higher confidence, capped at base_conf
                hit_boost = min(len(matches) * 0.02, 0.08)
                confidence = min(base_conf + hit_boost, 0.97)
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_type = doc_type
                    best_reason = f"Matched keywords: {', '.join(set(matches[:4]))}"

        return best_type, best_confidence, best_reason

    # ── Production: Azure OpenAI ──────────────────────────────────────────────

    def _classify_openai(self, doc: Document) -> Tuple[DocumentType, float, str]:
        try:
            from openai import AzureOpenAI  # type: ignore

            client = AzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_key,
                api_version="2024-02-15-preview",
            )

            # Truncate to avoid token limits — first 3000 chars usually enough to classify
            text_snippet = doc.text_content[:3000] if doc.text_content else doc.filename

            response = client.chat.completions.create(
                model=settings.azure_openai_deployment,
                messages=[
                    {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Document filename: {doc.filename}\n\n{text_snippet}"},
                ],
                temperature=0,           # Deterministic classification
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)
            doc_type = DocumentType(result.get("doc_type", "unknown"))
            confidence = float(result.get("confidence", 0.5))
            reasoning = result.get("reasoning", "")
            return doc_type, confidence, reasoning

        except Exception as exc:
            log_warning("classifier.openai_failed", doc.id, error=str(exc))
            # Graceful degradation: fall back to heuristic rather than hard-fail
            return self._classify_local(doc)
