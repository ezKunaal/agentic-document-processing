"""
Extractor Agent

Responsibility: given a classified document, extract structured fields
defined in config/extraction_schemas.json.

Real Azure path:
  → Azure Document Intelligence (Form Recognizer) for PDFs/images
  → Azure OpenAI for emails and free-text documents

Local dev path:
  → Regex + GPT-style prompt simulation on plain text

Adding a new document type = add a new entry in extraction_schemas.json.
No code changes needed.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict

from config.models import Document, DocumentType, ProcessingStatus
from config.settings import settings
from observability.logger import log_info, log_warning, trace_step

# Load schemas once at import time
_SCHEMAS_PATH = Path(__file__).parent.parent / "config" / "extraction_schemas.json"
EXTRACTION_SCHEMAS: Dict[str, Any] = json.loads(_SCHEMAS_PATH.read_text())


class ExtractorAgent:
    """
    Extracts structured key-value fields from a document.
    Writes results into doc.extracted_fields.
    """

    def run(self, doc: Document) -> Document:
        with trace_step("extractor", "extract", doc.id):
            doc.status = ProcessingStatus.EXTRACTING
            doc.add_audit("extractor", "started", f"doc_type={doc.doc_type.value}")

            schema = EXTRACTION_SCHEMAS.get(doc.doc_type.value)
            if not schema:
                log_warning("extractor.no_schema", doc.id, doc_type=doc.doc_type.value)
                doc.extracted_fields = {"raw_text": doc.text_content[:500]}
                doc.add_audit("extractor", "skipped", "no schema for doc type")
                return doc

            if settings.local_dev:
                fields = self._extract_local(doc, schema)
            else:
                fields = self._extract_azure(doc, schema)

            doc.extracted_fields = fields
            doc.add_audit(
                "extractor",
                "completed",
                f"extracted {len(fields)} fields: {list(fields.keys())}",
            )
            log_info("extractor.result", doc.id, fields=list(fields.keys()))

        return doc

    # ── Local dev: regex patterns per document type ───────────────────────────

    def _extract_local(self, doc: Document, schema: Dict[str, Any]) -> Dict[str, Any]:
        text = doc.text_content
        doc_type = doc.doc_type
        fields: Dict[str, Any] = {}

        if doc_type == DocumentType.INVOICE:
            fields = self._extract_invoice(text)
        elif doc_type == DocumentType.CONTRACT:
            fields = self._extract_contract(text)
        elif doc_type == DocumentType.EMAIL:
            fields = self._extract_email(text)
        elif doc_type == DocumentType.COMPLIANCE:
            fields = self._extract_compliance(text)

        # Fill missing required fields with None so validator can catch them
        for f in schema.get("required_fields", []):
            if f not in fields:
                fields[f] = None

        return fields

    def _extract_invoice(self, text: str) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}

        # Vendor name — look for "From:", "Vendor:", "Bill From:"
        m = re.search(r"(?:vendor|from|bill\s+from)[:\s]+([A-Za-z0-9\s&.,Ltd]+)", text, re.I)
        fields["vendor_name"] = m.group(1).strip() if m else None

        # Invoice number — must look like INV-xxx or a numeric code
        m = re.search(r"(?:invoice\s*(?:#|no|number))[:\s]*([A-Z]{1,5}[\-\d]+)", text, re.I)
        fields["invoice_number"] = m.group(1).strip() if m else None

        # Amount — prefer "Total Amount Due" over "Subtotal"
        m = re.search(r"total\s+amount\s+due[:\s]*\$?([\d,]+\.?\d*)", text, re.I)
        if not m:
            m = re.search(r"(?:grand\s+total|amount\s+due)[:\s]*\$?([\d,]+\.?\d*)", text, re.I)
        if not m:
            m = re.search(r"(?<!\w)total[:\s]*\$?([\d,]+\.?\d*)", text, re.I)
        if m:
            try:
                fields["amount"] = float(m.group(1).replace(",", ""))
            except ValueError:
                fields["amount"] = None
        else:
            fields["amount"] = None

        # Due date
        m = re.search(r"(?:due\s+date|payment\s+due)[:\s]*([\d\/\-A-Za-z]+)", text, re.I)
        fields["due_date"] = m.group(1).strip() if m else None

        # Optional: PO number
        m = re.search(r"(?:po|purchase\s+order)\s*(?:#|no|number)?[:\s]*([A-Z0-9\-]+)", text, re.I)
        fields["po_number"] = m.group(1).strip() if m else None

        # Currency default
        m = re.search(r"\b(USD|GBP|EUR|AUD)\b", text, re.I)
        fields["currency"] = m.group(1).upper() if m else "USD"

        return fields

    def _extract_contract(self, text: str) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}

        m = re.search(r"between\s+([A-Za-z0-9\s&.,Ltd]+?)\s+\(Party A\)", text, re.I)
        if not m:
            m = re.search(r"party\s+a[:\s\)]+([A-Za-z0-9\s&.,Ltd]+?)(?:\s+and\s|\s*\n)", text, re.I)
        fields["party_a"] = m.group(1).strip() if m else None

        m = re.search(r"(?:and|party\s+b)[:\s]+([A-Za-z0-9\s&.,Ltd]+?)(?:\s*[\(,\n])", text, re.I)
        fields["party_b"] = m.group(1).strip() if m else None

        m = re.search(r"(?:effective\s+date|dated|date)[:\s]*([\d\/\-A-Za-z,]+)", text, re.I)
        fields["effective_date"] = m.group(1).strip() if m else None

        m = re.search(r"\b(NDA|MSA|SOW|SLA|service\s+agreement|non.disclosure)\b", text, re.I)
        fields["contract_type"] = m.group(1).upper() if m else "AGREEMENT"

        m = re.search(r"(?:signed?\s+by|signatory)[:\s]+([A-Za-z\s]+)", text, re.I)
        fields["signatory"] = m.group(1).strip() if m else None

        return fields

    def _extract_email(self, text: str) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}

        m = re.search(r"From:\s*(.+)", text, re.I)
        fields["sender"] = m.group(1).strip() if m else None

        m = re.search(r"Subject:\s*(.+)", text, re.I)
        fields["subject"] = m.group(1).strip() if m else None

        m = re.search(r"Date:\s*(.+)", text, re.I)
        fields["received_date"] = m.group(1).strip() if m else None

        m = re.search(r"To:\s*(.+)", text, re.I)
        fields["recipient"] = m.group(1).strip() if m else None

        # Body summary — first 200 chars after headers
        body_start = text.find("\n\n")
        if body_start > 0:
            fields["body_summary"] = text[body_start:body_start + 200].strip()

        return fields

    def _extract_compliance(self, text: str) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}

        m = re.search(r"(?:regulation|reg\.|gdpr|sox|iso)[:\s]*([A-Z0-9\-\/]+)", text, re.I)
        fields["regulation_ref"] = m.group(1).strip() if m else None

        m = re.search(r"(?:entity|company|organisation)[:\s]+([A-Za-z0-9\s&.,Ltd]+)", text, re.I)
        fields["entity"] = m.group(1).strip() if m else None

        m = re.search(r"(?:submission|filing|report)\s+date[:\s]*([\d\/\-A-Za-z,]+)", text, re.I)
        fields["submission_date"] = m.group(1).strip() if m else None

        m = re.search(r"(?:officer|responsible\s+person)[:\s]+([A-Za-z\s]+)", text, re.I)
        fields["officer_name"] = m.group(1).strip() if m else None

        return fields

    # ── Production: Azure Document Intelligence ───────────────────────────────

    def _extract_azure(self, doc: Document, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        In production:
        - PDFs/images → azure-ai-formrecognizer (prebuilt-invoice, prebuilt-document)
        - Plain text/email → Azure OpenAI with extraction prompt

        Falls back to local extractor if Azure credentials aren't available.
        """
        try:
            from azure.ai.formrecognizer import DocumentAnalysisClient  # type: ignore
            from azure.core.credentials import AzureKeyCredential      # type: ignore

            client = DocumentAnalysisClient(
                endpoint=settings.doc_intelligence_endpoint,
                credential=AzureKeyCredential(settings.doc_intelligence_key),
            )

            model_id = "prebuilt-invoice" if doc.doc_type == DocumentType.INVOICE else "prebuilt-document"

            poller = client.begin_analyze_document(model_id, doc.raw_content)
            result = poller.result()

            fields: Dict[str, Any] = {}
            if result.documents:
                for field_name, field_value in result.documents[0].fields.items():
                    fields[field_name.lower()] = field_value.value if field_value else None

            return fields

        except Exception as exc:
            log_warning("extractor.azure_failed", doc.id, error=str(exc))
            return self._extract_local(doc, schema)
