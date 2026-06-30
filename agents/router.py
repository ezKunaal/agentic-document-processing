"""
Router Agent

Responsibility: given a validated document, decide which downstream
system receives it and dispatch via the correct connector.

Design decisions:
  - Rules live in config/routing_rules.json — zero code changes to add routes
  - Each destination implements IDocumentDestination (open/closed principle)
  - The connector registry is a dict — adding a new system = one dict entry
  - Dispatch is async-safe; failures dead-letter back to Service Bus (prod)
"""
import json
from pathlib import Path
from typing import Dict, List

from config.models import (
    Document,
    DocumentType,
    DownstreamSystem,
    EscalationReason,
    ProcessingStatus,
    RoutingRule,
)
from observability.logger import log_info, log_warning, trace_step

_RULES_PATH = Path(__file__).parent.parent / "config" / "routing_rules.json"


def _load_rules() -> List[RoutingRule]:
    raw = json.loads(_RULES_PATH.read_text())
    return [
        RoutingRule(
            doc_type=DocumentType(r["doc_type"]),
            destination=DownstreamSystem(r["destination"]),
            conditions=r.get("conditions", {}),
            description=r.get("description", ""),
        )
        for r in raw
    ]


class RouterAgent:
    """
    Matches document to a routing rule and dispatches to downstream connector.
    """

    def __init__(self) -> None:
        self.rules: List[RoutingRule] = _load_rules()

        # Connector registry — swap stub for real connector in production
        from downstream.finance_api import FinanceConnector
        from downstream.crm_api import CRMConnector
        from downstream.compliance_api import ComplianceConnector

        self._connectors: Dict[DownstreamSystem, "IDocumentDestination"] = {
            DownstreamSystem.FINANCE: FinanceConnector(),
            DownstreamSystem.CRM: CRMConnector(),
            DownstreamSystem.COMPLIANCE: ComplianceConnector(),
        }

    def run(self, doc: Document) -> Document:
        with trace_step("router", "route", doc.id):
            doc.status = ProcessingStatus.ROUTING
            doc.add_audit("router", "started")

            destination = self._match_rule(doc)
            doc.destination = destination

            if destination == DownstreamSystem.UNROUTABLE:
                doc.status = ProcessingStatus.ESCALATED
                doc.escalation_reason = EscalationReason.UNKNOWN_DOC_TYPE
                doc.escalation_note = "No routing rule matched — escalating to human"
                doc.add_audit("router", "unroutable", "no matching rule")
                log_warning("router.unroutable", doc.id, doc_type=doc.doc_type.value)
                return doc

            connector = self._connectors.get(destination)
            if not connector:
                log_warning("router.no_connector", doc.id, destination=destination.value)
                doc.status = ProcessingStatus.FAILED
                return doc

            success, response = connector.send(doc)

            if success:
                doc.status = ProcessingStatus.ROUTED
                doc.add_audit(
                    "router",
                    "dispatched",
                    f"destination={destination.value} response={response}",
                )
                log_info("router.dispatched", doc.id, destination=destination.value)
            else:
                # Transient failure — in prod this re-queues to Service Bus
                doc.status = ProcessingStatus.FAILED
                doc.add_audit("router", "dispatch_failed", response)
                log_warning("router.dispatch_failed", doc.id, error=response)

        return doc

    def _match_rule(self, doc: Document) -> DownstreamSystem:
        """First-match wins. Rules are evaluated in config file order."""
        for rule in self.rules:
            if rule.doc_type == doc.doc_type:
                return rule.destination
        return DownstreamSystem.UNROUTABLE


# ── Interface that every connector must implement ─────────────────────────────

class IDocumentDestination:
    """
    Contract for all downstream connectors.
    Implementing this interface is all you need to add a new destination.
    """

    def send(self, doc: Document):
        raise NotImplementedError
