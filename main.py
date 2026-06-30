"""
FastAPI Application — Main Entry Point

Endpoints:
  POST /documents/upload      — ingest a document (file upload)
  POST /documents/text        — ingest a document as plain text (easy testing)
  GET  /documents/{id}        — get document state + audit trail
  GET  /documents/            — list all documents (audit view)

  GET  /review/               — list all documents pending human review
  GET  /review/{id}           — get a single document for review
  POST /review/{id}/resolve   — submit human corrections and re-process

  GET  /health                — health check
  GET  /metrics               — simple processing metrics

Run locally:
  uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

from config.models import Document, DocumentType
from ingestion.pipeline import DocumentPipeline, build_document_from_upload
from hitl.review_queue import ReviewQueue
from observability.logger import get_audit, list_audits, log_info

app = FastAPI(
    title="Agentic Document Processing API",
    description=(
        "Azure-native agentic pipeline: classify → extract → validate → route. "
        "Handles PDFs, emails, invoices, and contracts with human-in-the-loop escalation."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # restrict to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Singletons — initialised once at startup
pipeline = DocumentPipeline()
review_queue = pipeline.review_queue


# ── Request / Response models ─────────────────────────────────────────────────

class TextIngestionRequest(BaseModel):
    filename: str
    text_content: str
    content_type: str = "text/plain"


class ReviewResolutionRequest(BaseModel):
    reviewer: str = "human"
    doc_type: Optional[str] = None
    extracted_fields: Optional[Dict[str, Any]] = None


# ── Ingestion endpoints ───────────────────────────────────────────────────────

@app.post("/documents/upload", summary="Upload a document file for processing")
async def upload_document(file: UploadFile = File(...)):
    """
    Accepts any file (PDF, .eml, .txt, .docx).
    Runs the full pipeline synchronously and returns the result.

    In production this would be async — the file goes to Blob Storage,
    Event Grid fires, Service Bus queues it, an Azure Function processes it.
    For the demo, we run it inline for instant feedback.
    """
    content = await file.read()
    doc = build_document_from_upload(
        filename=file.filename or "unknown",
        content=content,
        content_type=file.content_type or "application/octet-stream",
    )

    log_info("api.upload.received", doc.id, filename=file.filename, size=len(content))
    result = pipeline.process(doc)
    return JSONResponse(content=result.to_dict(), status_code=_status_code(result))


@app.post("/documents/text", summary="Ingest a document as plain text (useful for testing)")
async def ingest_text(request: TextIngestionRequest):
    """
    Accepts plain text content — great for demos and automated tests
    without needing real file uploads.
    """
    doc = Document(
        filename=request.filename,
        content_type=request.content_type,
        text_content=request.text_content,
        raw_content=request.text_content.encode(),
    )

    log_info("api.text.received", doc.id, filename=request.filename)
    result = pipeline.process(doc)
    return JSONResponse(content=result.to_dict(), status_code=_status_code(result))


@app.get("/documents/", summary="List all processed documents")
async def list_documents():
    return JSONResponse(content=list_audits())


@app.get("/documents/{doc_id}", summary="Get a document's state and full audit trail")
async def get_document(doc_id: str):
    doc = get_audit(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return JSONResponse(content=doc)


# ── Human-in-the-loop endpoints ───────────────────────────────────────────────

@app.get("/review/", summary="List all documents pending human review")
async def list_pending_reviews():
    """
    Returns all escalated documents with SLA deadlines.
    This feeds the review dashboard (Power Apps in production).
    """
    return JSONResponse(content=review_queue.list_pending())


@app.get("/review/{doc_id}", summary="Get a single document for human review")
async def get_review_item(doc_id: str):
    item = review_queue.get(doc_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Review item {doc_id} not found")
    return JSONResponse(content=item)


@app.post("/review/{doc_id}/resolve", summary="Submit human corrections and re-process")
async def resolve_review(doc_id: str, request: ReviewResolutionRequest):
    """
    The human has reviewed the document and submits corrections.
    The document is corrected and re-injected into the pipeline from the
    validation step (skipping re-classification since human has confirmed type).

    Returns the final processed document.
    """
    corrections: Dict[str, Any] = {}
    if request.doc_type:
        corrections["doc_type"] = request.doc_type
    if request.extracted_fields:
        corrections["extracted_fields"] = request.extracted_fields

    corrected_doc = review_queue.resolve(
        doc_id=doc_id,
        corrections=corrections,
        reviewer=request.reviewer,
    )
    if not corrected_doc:
        raise HTTPException(
            status_code=404,
            detail=f"Document {doc_id} not found or already resolved",
        )

    # Re-inject at the validation step (human already confirmed the type)
    log_info("api.review.reinjecting", doc_id, reviewer=request.reviewer)
    corrected_doc = pipeline.validator.run(corrected_doc)
    corrected_doc = pipeline.router.run(corrected_doc)

    from observability.logger import persist_audit
    persist_audit(corrected_doc.id, corrected_doc.to_dict())

    return JSONResponse(content=corrected_doc.to_dict())


# ── Observability endpoints ───────────────────────────────────────────────────

@app.get("/health", summary="Health check")
async def health():
    return {"status": "healthy", "service": "doc-agent"}


@app.get("/metrics", summary="Processing metrics summary")
async def metrics():
    all_docs = list_audits()
    status_counts: Dict[str, int] = {}
    escalation_counts: Dict[str, int] = {}
    doc_type_counts: Dict[str, int] = {}

    for doc in all_docs.values():
        s = doc.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

        er = doc.get("escalation_reason")
        if er:
            escalation_counts[er] = escalation_counts.get(er, 0) + 1

        dt = doc.get("doc_type", "unknown")
        doc_type_counts[dt] = doc_type_counts.get(dt, 0) + 1

    return {
        "total_documents": len(all_docs),
        "by_status": status_counts,
        "by_doc_type": doc_type_counts,
        "escalation_reasons": escalation_counts,
        "pending_reviews": len(review_queue.list_pending()),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status_code(doc: Document) -> int:
    from config.models import ProcessingStatus
    if doc.status == ProcessingStatus.ROUTED:
        return 200
    if doc.status == ProcessingStatus.ESCALATED:
        return 202   # Accepted but pending human review
    if doc.status == ProcessingStatus.FAILED:
        return 500
    return 200


# ── Review Portal UI ─────────────────────────────────────────────────────────

@app.get("/portal", response_class=HTMLResponse, include_in_schema=False)
async def review_portal():
    """Human review portal — Approve/Reject UI for escalated documents."""
    from pathlib import Path
    portal_file = Path(__file__).parent / "static" / "review_portal.html"
    return HTMLResponse(content=portal_file.read_text())


@app.get("/api-docs", response_class=HTMLResponse, include_in_schema=False)
async def api_docs():
    """Beautiful custom API documentation page."""
    from pathlib import Path
    return HTMLResponse(content=(Path(__file__).parent / "static" / "api_docs.html").read_text())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
