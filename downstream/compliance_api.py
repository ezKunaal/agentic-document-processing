"""
Compliance API Connector (stub)

In production: POST to an internal audit register or a GRC platform
(ServiceNow GRC, MetricStream, etc.)
Every compliance document also gets a copy in the immutable audit log.
"""
import json
from typing import Tuple

from config.models import Document
from observability.logger import log_info


class ComplianceConnector:
    """Routes compliance and regulatory documents to the audit register."""

    ENDPOINT = "https://compliance.internal/api/v1/filings"

    def send(self, doc: Document) -> Tuple[bool, str]:
        payload = {
            "source": "doc-agent",
            "doc_id": doc.id,
            "regulation_ref": doc.extracted_fields.get("regulation_ref"),
            "entity": doc.extracted_fields.get("entity"),
            "submission_date": doc.extracted_fields.get("submission_date"),
            "officer_name": doc.extracted_fields.get("officer_name"),
            "jurisdiction": doc.extracted_fields.get("jurisdiction"),
            "audit_trail": doc.audit_trail,   # full trail included for regulators
        }

        log_info("compliance_api.received", doc.id, endpoint=self.ENDPOINT, payload={
            k: v for k, v in payload.items() if k != "audit_trail"
        })

        stub_response = {
            "status": "filed",
            "compliance_ref": f"COMP-{doc.id[:8].upper()}",
            "message": (
                f"Filing registered: {payload['regulation_ref']} "
                f"for {payload['entity']} on {payload['submission_date']}"
            ),
        }
        return True, json.dumps(stub_response)
