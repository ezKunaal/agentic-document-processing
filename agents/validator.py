"""
Validator Agent

Responsibility: apply business rules to extracted fields and decide
whether the document is valid to route, or needs escalation.

Rules live in config/extraction_schemas.json under "validation_rules".
Adding a new rule = edit the JSON. No code changes.

Current rules supported:
  - required fields must be non-null
  - amount_max: flag invoices above a threshold for CFO approval
  - require_po_above: invoices above X need a PO number
  - require_signatory: contracts must have a signatory
  - require_officer: compliance docs need an officer name
"""
import json
from pathlib import Path
from typing import Any, Dict, List

from config.models import (
    Document,
    EscalationReason,
    ProcessingStatus,
)
from config.settings import settings
from observability.logger import log_info, log_warning, trace_step

_SCHEMAS_PATH = Path(__file__).parent.parent / "config" / "extraction_schemas.json"
EXTRACTION_SCHEMAS: Dict[str, Any] = json.loads(_SCHEMAS_PATH.read_text())

# Invoices above this need human approval regardless of confidence
APPROVAL_AMOUNT_THRESHOLD = 50_000.0


class ValidatorAgent:
    """
    Validates extracted fields against schema rules.
    Sets doc.validation_passed and doc.validation_errors.
    """

    def run(self, doc: Document) -> Document:
        with trace_step("validator", "validate", doc.id):
            doc.status = ProcessingStatus.VALIDATING
            doc.add_audit("validator", "started")

            errors: List[str] = []
            schema = EXTRACTION_SCHEMAS.get(doc.doc_type.value, {})

            # 1. Required fields check
            errors += self._check_required_fields(doc.extracted_fields, schema)

            # 2. Business rule checks
            rules = schema.get("validation_rules", {})
            errors += self._apply_business_rules(doc, rules)

            doc.validation_errors = errors
            doc.validation_passed = len(errors) == 0

            if doc.validation_passed:
                doc.add_audit("validator", "passed", "all rules satisfied")
                log_info("validator.passed", doc.id)
            else:
                doc.add_audit("validator", "failed", f"errors: {errors}")
                log_warning("validator.failed", doc.id, errors=errors)

        return doc

    def _check_required_fields(
        self, fields: Dict[str, Any], schema: Dict[str, Any]
    ) -> List[str]:
        errors = []
        for field_name in schema.get("required_fields", []):
            value = fields.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"Required field missing or empty: '{field_name}'")
        return errors

    def _apply_business_rules(
        self, doc: Document, rules: Dict[str, Any]
    ) -> List[str]:
        errors = []
        fields = doc.extracted_fields

        # Rule: invoice amount cannot exceed max
        if "amount_max" in rules:
            amount = fields.get("amount")
            if isinstance(amount, (int, float)) and amount > rules["amount_max"]:
                errors.append(
                    f"Invoice amount {amount} exceeds maximum {rules['amount_max']}"
                )
                doc.escalation_reason = EscalationReason.AMOUNT_EXCEEDS_THRESHOLD

        # Rule: PO number required above threshold
        if "require_po_above" in rules:
            amount = fields.get("amount")
            po = fields.get("po_number")
            if isinstance(amount, (int, float)) and amount > rules["require_po_above"]:
                if not po:
                    errors.append(
                        f"PO number required for invoices above {rules['require_po_above']}"
                    )

        # Rule: contracts must have a signatory
        if rules.get("require_signatory"):
            if not fields.get("signatory"):
                errors.append("Contract is missing a required signatory")

        # Rule: compliance docs must have a responsible officer
        if rules.get("require_officer"):
            if not fields.get("officer_name"):
                errors.append("Compliance document is missing a responsible officer name")

        # Rule: high-value invoice always needs human approval
        amount = fields.get("amount")
        if isinstance(amount, (int, float)) and amount > APPROVAL_AMOUNT_THRESHOLD:
            errors.append(
                f"Invoice amount ${amount:,.2f} exceeds ${APPROVAL_AMOUNT_THRESHOLD:,.0f} "
                "— requires human approval"
            )
            doc.escalation_reason = EscalationReason.AMOUNT_EXCEEDS_THRESHOLD

        return errors
