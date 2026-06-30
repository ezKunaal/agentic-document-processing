# Agentic Document Processing — Azure

An enterprise-grade agentic pipeline that ingests mixed document batches (PDFs, emails, invoices, contracts), autonomously classifies, extracts, validates, and routes them to downstream systems — with human-in-the-loop escalation for ambiguous or high-risk cases.

Built to demonstrate: **how to build, extend, secure, and scale** an agentic document solution on Azure.

---

## Live Demo

| What | Link |
|---|---|
| Custom API Documentation | `http://localhost:8000/api-docs` |
| Human Review Portal | `http://localhost:8000/portal` |
| Swagger UI | `http://localhost:8000/docs` |

*(Run locally — see Quick Start below. Public deployment instructions in `/azure`.)*

---

## Architecture

```
Documents (PDF / email / invoice / contract)
        │
        ▼
┌─────────────────────────────────────────────┐
│  Layer 1 — Ingestion                        │
│  Blob Storage → Event Grid → Service Bus    │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│  Layer 2 — AI Agent Orchestration           │
│  Classifier → Extractor → Validator → Router│
│  (Azure OpenAI + Document Intelligence)     │
└──────┬────────────────────────┬─────────────┘
       │ low confidence /       │ valid + confident
       │ validation fail        ▼
       ▼              ┌─────────────────────┐
┌─────────────┐       │  Layer 4 — Routing  │
│  Layer 3    │       │  Finance / CRM /    │
│  HITL       │       │  Compliance APIs    │
│  Review     │       └─────────────────────┘
│  Queue      │
└──────┬──────┘
       │ human corrects → re-injects at validator
       └──────────────────────────────────────────►
                                     ┌─────────────────────────────┐
                                     │  Layer 5 — Observability    │
                                     │  Monitor · Audit · Security │
                                     └─────────────────────────────┘
```

---

## Project Structure

```
doc-agent/
├── main.py                         # FastAPI app — all REST endpoints
├── Azure_Demo.ipynb                # Jupyter notebook — guided live demo
│
├── config/
│   ├── models.py                   # Domain models (Document, DocumentType, ...)
│   ├── settings.py                 # Environment-driven configuration (Azure-mapped)
│   ├── routing_rules.json          # Routing config — add routes here, no code changes
│   └── extraction_schemas.json     # Field schemas per doc type — add types here
│
├── agents/
│   ├── classifier.py               # Step 1: classify document type + confidence score
│   ├── extractor.py                # Step 2: extract structured fields
│   ├── validator.py                # Step 3: apply business rules
│   └── router.py                   # Step 4: dispatch to downstream connector
│
├── ingestion/
│   └── pipeline.py                 # Orchestrator — chains agents, handles escalation
│
├── hitl/
│   └── review_queue.py             # Human-in-the-loop queue + re-injection logic
│
├── downstream/
│   ├── finance_api.py              # Finance connector (SAP/D365 stub)
│   ├── crm_api.py                  # CRM connector (Salesforce stub)
│   └── compliance_api.py           # Compliance register connector (stub)
│
├── observability/
│   └── logger.py                   # Structured JSON logging + audit persistence
│
├── static/
│   ├── review_portal.html          # Human review UI — Approve/Reject workflow
│   └── api_docs.html               # Custom branded API documentation page
│
├── azure/
│   ├── main.bicep                  # Full Azure infrastructure as code
│   ├── parameters.json             # Bicep deployment parameters
│   └── deploy.sh                   # End-to-end Azure deployment script
│
├── tests/
│   └── test_pipeline.py            # 31 tests covering all layers
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Quick Start

### Option A — Docker (recommended)

```bash
git clone https://github.com/ezKunaal/agentic-document-processing.git
cd agentic-document-processing
docker-compose up --build
```

API available at: `http://localhost:8000`
Custom API Docs at: `http://localhost:8000/api-docs`
Review Portal at: `http://localhost:8000/portal`

### Option B — Local Python

```bash
cd agentic-document-processing
pip install -r requirements.txt
cp .env.example .env        # edit if connecting to real Azure services
uvicorn main:app --reload --port 8000
```

### Option C — Jupyter Notebook (guided demo)

```bash
jupyter notebook Azure_Demo.ipynb
```

Run cells in order — starts the server, walks through classification, extraction, escalation, human review, and metrics with explanations at each step.

### Run Tests

```bash
pytest tests/ -v
# 31 passed — no Azure credentials required (LOCAL_DEV=true)
```

---

## API Endpoints

### Ingest Documents

```bash
# Upload a file
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@invoice.pdf"

# Or POST plain text (great for testing)
curl -X POST http://localhost:8000/documents/text \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "invoice.txt",
    "text_content": "INVOICE\nVendor: Acme Ltd\nInvoice No: INV-001\nTotal Amount Due: $8250\nDue Date: 2024-02-15\nPO Number: PO-123"
  }'
```

**Response (routed):**
```json
{
  "id": "abc123",
  "filename": "invoice.txt",
  "doc_type": "invoice",
  "confidence": 0.92,
  "extracted_fields": { "vendor_name": "Acme Ltd", "amount": 8250.0 },
  "validation_passed": true,
  "destination": "finance",
  "status": "routed",
  "audit_trail": []
}
```

**Response (escalated — high-value invoice):**
```json
{
  "status": "escalated",
  "escalation_reason": "amount_exceeds_threshold",
  "escalation_note": "Invoice amount $75,000 exceeds $50,000 — requires human approval"
}
```

### Human Review Queue

```bash
# List all escalated documents
curl http://localhost:8000/review/

# Get one for review (includes text snippet, AI guess, validation errors)
curl http://localhost:8000/review/{doc_id}

# Human submits correction and re-processes
curl -X POST http://localhost:8000/review/{doc_id}/resolve \
  -H "Content-Type: application/json" \
  -d '{
    "reviewer": "jane.smith",
    "doc_type": "invoice",
    "extracted_fields": {
      "vendor_name": "BigCorp International",
      "invoice_number": "INV-999",
      "amount": 8000.00,
      "due_date": "2024-03-01",
      "po_number": "PO-APPROVED-001"
    }
  }'
```

### Observability

```bash
curl http://localhost:8000/health
curl http://localhost:8000/metrics
curl http://localhost:8000/documents/{doc_id}
```

---

## How to Extend

This is the core requirement the system was designed around: **adding new document types and routing rules with zero changes to agent logic.**

### Add a New Document Type

1. Add an entry to `config/extraction_schemas.json`:
```json
"purchase_order": {
  "required_fields": ["supplier", "po_number", "delivery_date", "total_value"],
  "optional_fields": ["line_items", "payment_terms"],
  "validation_rules": { "require_approval_above": 25000 }
}
```

2. Add a routing rule to `config/routing_rules.json`:
```json
{ "doc_type": "purchase_order", "destination": "finance", "description": "POs go to Finance" }
```

3. Add the new type to the `DocumentType` enum in `config/models.py`.

**That's it. No agent code changes.** The Classifier, Extractor, Validator, and Router all read these configs at runtime.

### Add a New Downstream System

1. Create `downstream/procurement_api.py` implementing the shared interface:
```python
class ProcurementConnector:
    def send(self, doc: Document) -> Tuple[bool, str]:
        # POST to your Procurement endpoint
        ...
```

2. Register it in `agents/router.py`:
```python
DownstreamSystem.PROCUREMENT: ProcurementConnector(),
```

3. Add the routing rule to `routing_rules.json`.

This pattern was demonstrated live during the technical interview — a brand new "Purchase Order" document type and "Procurement" downstream system were added in under 5 minutes without touching the classifier, extractor, validator, or router logic.

---

## Azure Deployment

Full infrastructure-as-code is provided in `/azure`:

```bash
cd azure
az login
az group create --name rg-doc-agent --location australiaeast
./deploy.sh
```

This provisions:
- Azure Blob Storage + Event Grid + Service Bus
- Azure OpenAI (GPT-4o) + Document Intelligence
- Azure Container Apps (scales 0→20)
- Azure Key Vault + Managed Identity
- Cosmos DB (audit trail)
- Application Insights + Log Analytics

See `azure/main.bicep` for the full resource definitions and `azure/deploy.sh` for the step-by-step deployment script.

### Connecting to Real Azure Services Locally

Set `LOCAL_DEV=false` and fill in `.env`:

```bash
AZURE_OPENAI_ENDPOINT=https://your-instance.openai.azure.com/
AZURE_OPENAI_KEY=<from Key Vault>
AZURE_OPENAI_DEPLOYMENT=gpt-4o

DOC_INTELLIGENCE_ENDPOINT=https://your-instance.cognitiveservices.azure.com/
DOC_INTELLIGENCE_KEY=<from Key Vault>

SERVICE_BUS_CONNECTION_STRING=Endpoint=sb://...
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;...
```

Uncomment the Azure SDK packages in `requirements.txt` and reinstall. The code automatically uses real Azure services when `LOCAL_DEV=false` — every agent has an `_azure` path that activates.

---

## Security Design

| Concern | Solution |
|---|---|
| Secrets management | Azure Key Vault — no secrets in code or env files in production |
| Identity | Managed Identity (no API keys in code) |
| Data isolation | RBAC — Finance staff cannot access Compliance documents |
| Audit trail | Immutable append-only log in Cosmos DB, per document |
| Network | Private endpoints for all Azure services; no public internet exposure |
| Threat detection | Microsoft Defender for Cloud on storage and compute |

---

## Scaling on Azure

| Component | Scale strategy |
|---|---|
| Ingestion | Azure Blob Storage + Event Grid — scales to millions of events/day |
| Processing | Azure Container Apps with Service Bus trigger — scales to zero, bursts automatically |
| AI inference | Azure OpenAI provisioned throughput (PTU) for predictable latency |
| Queue backpressure | Service Bus dead-letter queue — no documents lost under load |
| Review queue | Azure Service Bus + Power Apps — scales to any volume |
| Audit log | Cosmos DB with time-series partitioning — queryable at enterprise scale |

---

## Design Decisions

**Why Service Bus between ingestion and processing?**
Durability and retry. If the AI agent crashes mid-processing, the message returns to the queue. No document is lost. Event Grid alone gives you fan-out; Service Bus gives you reliable exactly-once delivery.

**Why is confidence score the central gate?**
It externalises uncertainty. Instead of guessing and being silently wrong, the system measures its own confidence and routes low-confidence cases to humans. This is what makes the system trustworthy in an enterprise context.

**Why are routing rules in JSON and not code?**
Open/closed principle. The system is open to extension (add a new route) but closed to modification (don't touch the agent code). Business users or config management can add routes without a deployment.

**Why does escalation live in the orchestrator and not the agents?**
Single responsibility. Each agent has one job: classify, extract, validate, or route. The decision of what to do when things go wrong is a cross-cutting concern that belongs in the orchestrator. This keeps agents testable in isolation.

**Why does the human review re-inject at the validator, not the classifier?**
Because the human has confirmed the document type. Re-running the classifier would be wasteful and would ignore the human's input. We trust the human's correction and skip straight to validation with their corrected fields.

---

## Tech Stack

Python · FastAPI · Azure OpenAI (GPT-4o) · Azure Document Intelligence · Azure Service Bus · Azure Container Apps · Azure Key Vault · Cosmos DB · Docker · pytest
