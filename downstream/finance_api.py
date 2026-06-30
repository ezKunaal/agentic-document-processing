"""
Finance API Connector (stub)

In production: POST to SAP REST API or Microsoft D365 Finance endpoint.
Auth via OAuth2 client credentials, secrets from Azure Key Vault.

Stub behaviour: logs the payload and returns success.
"""
import json
from typing import Tuple

from config.models import Document
from observability.logger import log_info


class FinanceConnector:
    """Routes invoices and payment documents to the Finance system (SAP/D365)."""

    ENDPOINT = "https://finance.internal/api/v1/invoices"   # real URL in prod

    def send(self, doc: Document) -> Tuple[bool, str]:
        payload = {
            "source": "doc-agent",
            "doc_id": doc.id,
            "doc_type": doc.doc_type.value,
            "vendor_name": doc.extracted_fields.get("vendor_name"),
            "invoice_number": doc.extracted_fields.get("invoice_number"),
            "amount": doc.extracted_fields.get("amount"),
            "due_date": doc.extracted_fields.get("due_date"),
            "po_number": doc.extracted_fields.get("po_number"),
            "currency": doc.extracted_fields.get("currency", "USD"),
        }

        # ── STUB: pretend we POSTed to SAP ───────────────────────────────────
        log_info(
            "finance_api.received",
            doc.id,
            endpoint=self.ENDPOINT,
            payload=payload,
        )
        # In production:
        #   resp = requests.post(self.ENDPOINT, json=payload, headers=self._auth_headers())
        #   return resp.ok, resp.text
        # ─────────────────────────────────────────────────────────────────────

        stub_response = {
            "status": "accepted",
            "finance_ref": f"FIN-{doc.id[:8].upper()}",
            "message": f"Invoice queued in Finance system for vendor: {payload['vendor_name']}",
        }
        return True, json.dumps(stub_response)

    def _auth_headers(self):
        # In production: fetch token from Key Vault, return Bearer header
        return {"Authorization": "Bearer <token-from-key-vault>"}
