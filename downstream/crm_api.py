"""
CRM API Connector (stub)

In production: Salesforce REST API or Microsoft D365 CRM.
Contracts create/update Account + Contract records.
Emails create Activity records linked to the Contact.
"""
import json
from typing import Tuple

from config.models import Document, DocumentType
from observability.logger import log_info


class CRMConnector:
    """Routes contracts and email correspondence to CRM (Salesforce/D365)."""

    CONTRACT_ENDPOINT = "https://crm.internal/api/v1/contracts"
    EMAIL_ENDPOINT    = "https://crm.internal/api/v1/activities"

    def send(self, doc: Document) -> Tuple[bool, str]:
        if doc.doc_type == DocumentType.CONTRACT:
            return self._send_contract(doc)
        return self._send_email(doc)

    def _send_contract(self, doc: Document) -> Tuple[bool, str]:
        payload = {
            "source": "doc-agent",
            "doc_id": doc.id,
            "contract_type": doc.extracted_fields.get("contract_type"),
            "party_a": doc.extracted_fields.get("party_a"),
            "party_b": doc.extracted_fields.get("party_b"),
            "effective_date": doc.extracted_fields.get("effective_date"),
            "signatory": doc.extracted_fields.get("signatory"),
        }

        log_info("crm_api.contract_received", doc.id, endpoint=self.CONTRACT_ENDPOINT, payload=payload)

        stub_response = {
            "status": "created",
            "crm_ref": f"CRM-CONT-{doc.id[:8].upper()}",
            "message": f"Contract record created between {payload['party_a']} and {payload['party_b']}",
        }
        return True, json.dumps(stub_response)

    def _send_email(self, doc: Document) -> Tuple[bool, str]:
        payload = {
            "source": "doc-agent",
            "doc_id": doc.id,
            "activity_type": "InboundEmail",
            "sender": doc.extracted_fields.get("sender"),
            "subject": doc.extracted_fields.get("subject"),
            "received_date": doc.extracted_fields.get("received_date"),
            "body_summary": doc.extracted_fields.get("body_summary"),
        }

        log_info("crm_api.email_received", doc.id, endpoint=self.EMAIL_ENDPOINT, payload=payload)

        stub_response = {
            "status": "created",
            "crm_ref": f"CRM-ACT-{doc.id[:8].upper()}",
            "message": f"Email activity logged from {payload['sender']}",
        }
        return True, json.dumps(stub_response)
