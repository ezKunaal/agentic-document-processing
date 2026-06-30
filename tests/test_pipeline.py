"""
Test suite for the document processing pipeline.

Runs fully in local dev mode — no Azure credentials required.
Tests cover: classification, extraction, validation, routing, HITL, and API.

Run with:
  pytest tests/ -v
"""
import pytest
from fastapi.testclient import TestClient

from config.models import (
    Document,
    DocumentType,
    DownstreamSystem,
    EscalationReason,
    ProcessingStatus,
)
from config.settings import settings
from agents.classifier import ClassifierAgent
from agents.extractor import ExtractorAgent
from agents.validator import ValidatorAgent
from agents.router import RouterAgent
from ingestion.pipeline import DocumentPipeline, build_document_from_upload
from hitl.review_queue import ReviewQueue
from main import app

# Ensure local dev mode for all tests
settings.local_dev = True
settings.confidence_threshold = 0.80

client = TestClient(app)


# ── Sample document text fixtures ─────────────────────────────────────────────

INVOICE_TEXT = """
INVOICE

Vendor: Acme Supplies Ltd
Invoice No: INV-2024-001
Invoice Date: 2024-01-15
Due Date: 2024-02-15

Bill To: Rapid Circle BV

Description                  Qty    Unit Price    Total
Cloud infrastructure setup    1     $5,000.00   $5,000.00
Managed services (monthly)    1     $2,500.00   $2,500.00

PO Number: PO-9876
Subtotal: $7,500.00
Tax (10%): $750.00
Total Amount Due: $8,250.00

Payment Terms: Net 30
Currency: USD
"""

HIGH_VALUE_INVOICE_TEXT = """
INVOICE

Vendor: BigCorp International
Invoice No: INV-2024-999
Due Date: 2024-03-01
Total Amount Due: $75,000.00

Note: Payment required within 7 days.
"""

CONTRACT_TEXT = """
SERVICE AGREEMENT (NDA)

This Agreement is made between Rapid Circle BV (Party A)
and TechPartner Ltd (Party B), effective date: 2024-01-01.

Signed by: Jane Smith, Director
Governing Law: Netherlands
"""

EMAIL_TEXT = """
From: customer@example.com
To: support@rapidcircle.com
Subject: Help with my subscription
Date: Mon, 15 Jan 2024 09:30:00 +0000

Dear Support Team,

I would like to upgrade my subscription plan.
Please advise on the available options.

Kind regards,
John Customer
"""

COMPLIANCE_TEXT = """
GDPR Compliance Filing

Regulation: GDPR-2018/EU
Entity: Rapid Circle BV
Submission Date: 2024-01-31
Jurisdiction: European Union
Responsible Officer: Maria Compliance

This filing confirms adherence to all GDPR data processing requirements.
"""

AMBIGUOUS_TEXT = "This document contains some random content without clear signals."


# ── Classifier tests ──────────────────────────────────────────────────────────

class TestClassifierAgent:
    def setup_method(self):
        self.agent = ClassifierAgent()

    def _make_doc(self, text: str, filename: str = "test.txt") -> Document:
        return Document(filename=filename, text_content=text)

    def test_classifies_invoice(self):
        doc = self._make_doc(INVOICE_TEXT, "invoice.pdf")
        result = self.agent.run(doc)
        assert result.doc_type == DocumentType.INVOICE
        assert result.confidence >= 0.85

    def test_classifies_contract(self):
        doc = self._make_doc(CONTRACT_TEXT, "contract.pdf")
        result = self.agent.run(doc)
        assert result.doc_type == DocumentType.CONTRACT
        assert result.confidence >= 0.85

    def test_classifies_email(self):
        doc = self._make_doc(EMAIL_TEXT, "email.eml")
        result = self.agent.run(doc)
        assert result.doc_type == DocumentType.EMAIL
        assert result.confidence >= 0.80

    def test_classifies_compliance(self):
        doc = self._make_doc(COMPLIANCE_TEXT, "gdpr.pdf")
        result = self.agent.run(doc)
        assert result.doc_type == DocumentType.COMPLIANCE
        assert result.confidence >= 0.80

    def test_low_confidence_for_ambiguous(self):
        doc = self._make_doc(AMBIGUOUS_TEXT, "mystery.txt")
        result = self.agent.run(doc)
        # Should have lower confidence — exact type doesn't matter
        assert result.confidence < 0.90

    def test_audit_trail_populated(self):
        doc = self._make_doc(INVOICE_TEXT)
        result = self.agent.run(doc)
        assert len(result.audit_trail) >= 2
        agents = [a.agent for a in result.audit_trail]
        assert "classifier" in agents


# ── Extractor tests ───────────────────────────────────────────────────────────

class TestExtractorAgent:
    def setup_method(self):
        self.agent = ExtractorAgent()

    def test_extracts_invoice_fields(self):
        doc = Document(
            filename="invoice.pdf",
            text_content=INVOICE_TEXT,
            doc_type=DocumentType.INVOICE,
            confidence=0.92,
        )
        result = self.agent.run(doc)
        assert result.extracted_fields.get("vendor_name") is not None
        assert result.extracted_fields.get("invoice_number") == "INV-2024-001"
        assert result.extracted_fields.get("amount") == 8250.0
        assert result.extracted_fields.get("po_number") == "PO-9876"

    def test_extracts_contract_fields(self):
        doc = Document(
            filename="contract.pdf",
            text_content=CONTRACT_TEXT,
            doc_type=DocumentType.CONTRACT,
            confidence=0.91,
        )
        result = self.agent.run(doc)
        assert result.extracted_fields.get("contract_type") is not None
        assert result.extracted_fields.get("signatory") is not None

    def test_extracts_email_fields(self):
        doc = Document(
            filename="email.eml",
            text_content=EMAIL_TEXT,
            doc_type=DocumentType.EMAIL,
            confidence=0.88,
        )
        result = self.agent.run(doc)
        assert "customer@example.com" in (result.extracted_fields.get("sender") or "")
        assert result.extracted_fields.get("subject") is not None

    def test_unknown_type_handled_gracefully(self):
        doc = Document(
            filename="mystery.txt",
            text_content="Some random text",
            doc_type=DocumentType.UNKNOWN,
            confidence=0.40,
        )
        result = self.agent.run(doc)
        # Should not raise — just returns minimal fields
        assert isinstance(result.extracted_fields, dict)


# ── Validator tests ───────────────────────────────────────────────────────────

class TestValidatorAgent:
    def setup_method(self):
        self.agent = ValidatorAgent()

    def test_valid_invoice_passes(self):
        doc = Document(
            filename="invoice.pdf",
            doc_type=DocumentType.INVOICE,
            confidence=0.92,
            extracted_fields={
                "vendor_name": "Acme Ltd",
                "invoice_number": "INV-001",
                "amount": 8250.0,
                "due_date": "2024-02-15",
                "po_number": "PO-9876",
            },
        )
        result = self.agent.run(doc)
        assert result.validation_passed is True
        assert result.validation_errors == []

    def test_missing_required_field_fails(self):
        doc = Document(
            filename="invoice.pdf",
            doc_type=DocumentType.INVOICE,
            confidence=0.92,
            extracted_fields={
                "vendor_name": "Acme Ltd",
                # invoice_number and amount missing
                "due_date": "2024-02-15",
            },
        )
        result = self.agent.run(doc)
        assert result.validation_passed is False
        assert any("invoice_number" in e for e in result.validation_errors)

    def test_high_value_invoice_escalated(self):
        doc = Document(
            filename="big_invoice.pdf",
            doc_type=DocumentType.INVOICE,
            confidence=0.93,
            extracted_fields={
                "vendor_name": "BigCorp",
                "invoice_number": "INV-999",
                "amount": 75000.0,
                "due_date": "2024-03-01",
            },
        )
        result = self.agent.run(doc)
        assert result.validation_passed is False
        assert result.escalation_reason == EscalationReason.AMOUNT_EXCEEDS_THRESHOLD

    def test_contract_missing_signatory_fails(self):
        doc = Document(
            filename="contract.pdf",
            doc_type=DocumentType.CONTRACT,
            confidence=0.90,
            extracted_fields={
                "party_a": "Rapid Circle",
                "party_b": "TechCo",
                "effective_date": "2024-01-01",
                "contract_type": "NDA",
                "signatory": None,
            },
        )
        result = self.agent.run(doc)
        assert result.validation_passed is False


# ── Full pipeline tests ───────────────────────────────────────────────────────

class TestPipeline:
    def setup_method(self):
        self.pipeline = DocumentPipeline()

    def test_invoice_routes_to_finance(self):
        doc = build_document_from_upload(
            "invoice.txt", INVOICE_TEXT.encode(), "text/plain"
        )
        result = self.pipeline.process(doc)
        assert result.status == ProcessingStatus.ROUTED
        assert result.destination == DownstreamSystem.FINANCE

    def test_contract_routes_to_crm(self):
        doc = build_document_from_upload(
            "contract.txt", CONTRACT_TEXT.encode(), "text/plain"
        )
        result = self.pipeline.process(doc)
        assert result.status == ProcessingStatus.ROUTED
        assert result.destination == DownstreamSystem.CRM

    def test_email_routes_to_crm(self):
        doc = build_document_from_upload(
            "email.txt", EMAIL_TEXT.encode(), "text/plain"
        )
        result = self.pipeline.process(doc)
        assert result.status == ProcessingStatus.ROUTED
        assert result.destination == DownstreamSystem.CRM

    def test_compliance_routes_to_compliance(self):
        doc = build_document_from_upload(
            "compliance.txt", COMPLIANCE_TEXT.encode(), "text/plain"
        )
        result = self.pipeline.process(doc)
        assert result.status == ProcessingStatus.ROUTED
        assert result.destination == DownstreamSystem.COMPLIANCE

    def test_high_value_invoice_escalates(self):
        doc = build_document_from_upload(
            "big_invoice.txt", HIGH_VALUE_INVOICE_TEXT.encode(), "text/plain"
        )
        result = self.pipeline.process(doc)
        assert result.status == ProcessingStatus.ESCALATED

    def test_audit_trail_complete(self):
        doc = build_document_from_upload(
            "invoice.txt", INVOICE_TEXT.encode(), "text/plain"
        )
        result = self.pipeline.process(doc)
        agents = {a.agent for a in result.audit_trail}
        # All four agents should have left entries
        assert "classifier" in agents
        assert "extractor" in agents
        assert "validator" in agents
        assert "router" in agents


# ── HITL tests ────────────────────────────────────────────────────────────────

class TestHITL:
    def setup_method(self):
        self.queue = ReviewQueue()

    def test_enqueue_and_list(self):
        doc = Document(filename="test.pdf", doc_type=DocumentType.UNKNOWN, confidence=0.4)
        self.queue.enqueue(doc)
        pending = self.queue.list_pending()
        ids = [p["doc_id"] for p in pending]
        assert doc.id in ids

    def test_resolve_with_correction(self):
        doc = Document(
            filename="ambiguous.pdf",
            doc_type=DocumentType.UNKNOWN,
            confidence=0.45,
            text_content=INVOICE_TEXT,
        )
        self.queue.enqueue(doc)
        corrected = self.queue.resolve(
            doc.id,
            corrections={
                "doc_type": "invoice",
                "extracted_fields": {"vendor_name": "Corrected Vendor", "amount": 100.0},
            },
            reviewer="test_reviewer",
        )
        assert corrected is not None
        assert corrected.doc_type == DocumentType.INVOICE
        assert corrected.confidence == 1.0
        assert corrected.extracted_fields["vendor_name"] == "Corrected Vendor"
        assert corrected.status == ProcessingStatus.HUMAN_REVIEWED


# ── API endpoint tests ────────────────────────────────────────────────────────

class TestAPI:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_ingest_text_invoice(self):
        resp = client.post("/documents/text", json={
            "filename": "invoice.txt",
            "text_content": INVOICE_TEXT,
        })
        assert resp.status_code in (200, 202)
        data = resp.json()
        assert data["doc_type"] == "invoice"
        assert data["destination"] == "finance"

    def test_ingest_text_contract(self):
        resp = client.post("/documents/text", json={
            "filename": "contract.txt",
            "text_content": CONTRACT_TEXT,
        })
        assert resp.status_code in (200, 202)
        data = resp.json()
        assert data["doc_type"] == "contract"

    def test_ingest_high_value_invoice_escalates(self):
        resp = client.post("/documents/text", json={
            "filename": "big_invoice.txt",
            "text_content": HIGH_VALUE_INVOICE_TEXT,
        })
        # 202 = Accepted but escalated
        data = resp.json()
        assert data["status"] == "escalated"

    def test_get_document(self):
        resp = client.post("/documents/text", json={
            "filename": "email.txt",
            "text_content": EMAIL_TEXT,
        })
        doc_id = resp.json()["id"]
        resp2 = client.get(f"/documents/{doc_id}")
        assert resp2.status_code == 200
        assert resp2.json()["id"] == doc_id

    def test_list_pending_reviews(self):
        # First create an escalation
        client.post("/documents/text", json={
            "filename": "big.txt",
            "text_content": HIGH_VALUE_INVOICE_TEXT,
        })
        resp = client.get("/review/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_metrics(self):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_documents" in data
        assert "by_status" in data
        assert "by_doc_type" in data

    def test_upload_file(self):
        content = INVOICE_TEXT.encode()
        resp = client.post(
            "/documents/upload",
            files={"file": ("invoice.txt", content, "text/plain")},
        )
        assert resp.status_code in (200, 202)
        assert resp.json()["doc_type"] == "invoice"

    def test_full_hitl_flow_via_api(self):
        """
        Full end-to-end: ingest ambiguous doc → appears in review queue
        → human resolves → document gets routed.
        """
        # 1. Ingest a high-value invoice (will escalate)
        resp = client.post("/documents/text", json={
            "filename": "big_invoice.txt",
            "text_content": HIGH_VALUE_INVOICE_TEXT,
        })
        doc_id = resp.json()["id"]
        assert resp.json()["status"] == "escalated"

        # 2. Check it's in the review queue
        pending = client.get("/review/").json()
        pending_ids = [p["doc_id"] for p in pending]
        assert doc_id in pending_ids

        # 3. Human reviews and resolves with corrected fields
        resolve_resp = client.post(f"/review/{doc_id}/resolve", json={
            "reviewer": "test_human",
            "doc_type": "invoice",
            "extracted_fields": {
                "vendor_name": "BigCorp International",
                "invoice_number": "INV-2024-999",
                "amount": 8000.0,   # corrected — below threshold now
                "due_date": "2024-03-01",
                "po_number": "PO-APPROVED-001",
            },
        })
        assert resolve_resp.status_code == 200
        final = resolve_resp.json()
        assert final["status"] == "routed"
        assert final["destination"] == "finance"
