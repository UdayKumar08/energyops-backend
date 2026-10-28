"""Documents an organisation uploads, and search over them.

Search is extractive: the answer is the organisation's own passages, as written, with the
document each came from. Documents belong to the whole organisation, not to a site.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from energyops.db import repo
from energyops.db.models import Document
from energyops.knowledge import (
    MAX_DOCUMENT_BYTES,
    DocumentRefused,
    Embedder,
    EmbeddingError,
    decode_document,
    has_content,
    prepare_chunks,
)
from energyops.security.permissions import Permission
from energyops.security.principal import Principal
from energyops.service.deps import needs, state_of
from energyops.service.state import ServiceState
from energyops.service.uploads import read_capped

router = APIRouter()
Viewer = Annotated[Principal, Depends(needs(Permission.VIEW_DATA))]
Manager = Annotated[Principal, Depends(needs(Permission.MANAGE_KNOWLEDGE))]

MAX_DOCUMENTS = 200
MIN_SCORE = 0.05
LEXICAL_NOTE = (
    "This search matches shared wording only; it does not understand meaning. A question that "
    "uses different words from the document may find nothing."
)


class DocumentView(BaseModel):
    id: str
    title: str
    filename: str | None
    bytes: int
    passages: int
    searchable: bool  # False when another embedder made its vectors
    uploaded_at: datetime


class PassageView(BaseModel):
    document_id: str
    title: str
    position: int
    text: str
    score: float


class SearchResult(BaseModel):
    question: str
    results: list[PassageView]
    semantic: bool
    note: str | None
    unsearchable_documents: int


def embedder_of(state: ServiceState) -> Embedder:
    if state.embedder is None:
        raise HTTPException(
            status_code=503, detail="Knowledge search has no embedding provider configured."
        )
    return state.embedder


def _view(doc: Document, embedder_name: str | None) -> DocumentView:
    return DocumentView(
        id=str(doc.id),
        title=doc.title,
        filename=doc.filename,
        bytes=doc.byte_size,
        passages=doc.chunk_count,
        searchable=doc.embedder == embedder_name,
        uploaded_at=doc.created_at,
    )


@router.post("/knowledge/documents", status_code=201)
async def upload_document(
    principal: Manager,
    request: Request,
    title: Annotated[str, Query(min_length=1, max_length=200)],
    filename: Annotated[str | None, Query(max_length=200)] = None,
) -> DocumentView:
    """Send plain text or markdown as the request body."""
    state = state_of(request)
    embedder = embedder_of(state)
    org = principal.org()
    data = await read_capped(request, MAX_DOCUMENT_BYTES + 1024)
    try:
        passages = [p for p in prepare_chunks(decode_document(data)) if has_content(p)]
    except DocumentRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not passages:
        raise HTTPException(status_code=422, detail="The document has no words to search.")
    async with state.db.session(principal.scope) as session:
        if len(await repo.list_documents(session, org)) >= MAX_DOCUMENTS:
            raise HTTPException(
                status_code=409, detail=f"An organisation can keep {MAX_DOCUMENTS} documents."
            )
    try:
        vectors = await embedder.embed(passages)
    except EmbeddingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    async with state.db.session(principal.scope) as session:
        document = await repo.create_document(
            session,
            org_id=org,
            title=title.strip(),
            filename=filename,
            byte_size=len(data),
            embedder=embedder.name,
            uploaded_by=principal.user_id,
            passages=passages,
            vectors=vectors,
        )
        await repo.audit(
            session,
            org_id=org,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action="knowledge.upload",
            target_type="document",
            target_id=str(document.id),
            detail={"title": document.title, "passages": len(passages), "embedder": embedder.name},
        )
    return _view(document, embedder.name)


@router.get("/knowledge/documents")
async def list_documents(principal: Viewer, request: Request) -> list[DocumentView]:
    state = state_of(request)
    async with state.db.session(principal.scope) as session:
        documents = await repo.list_documents(session, principal.org())
    current = state.embedder.name if state.embedder else None
    return [_view(d, current) for d in documents]


@router.delete("/knowledge/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, principal: Manager, request: Request) -> Response:
    state = state_of(request)
    org = principal.org()
    async with state.db.session(principal.scope) as session:
        document = await repo.get_document(session, org, document_id)
        if document is None or not await repo.delete_document(session, org, document_id):
            raise HTTPException(status_code=404, detail="No document with that id.")
        await repo.audit(
            session,
            org_id=org,
            actor_id=principal.user_id,
            actor_email=principal.email,
            action="knowledge.delete",
            target_type="document",
            target_id=str(document_id),
            detail={"title": document.title},
        )
    return Response(status_code=204)


async def search_knowledge(
    state: ServiceState, principal: Principal, question: str, limit: int = 5
) -> SearchResult:
    embedder = embedder_of(state)
    org = principal.org()
    hits: list[repo.PassageHit] = []
    if has_content(question):
        try:
            (vector,) = await embedder.embed([question])
        except EmbeddingError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        async with state.db.session(principal.scope) as session:
            hits = await repo.search_chunks(
                session,
                org,
                embedder=embedder.name,
                vector=vector,
                limit=limit,
                min_score=MIN_SCORE,
            )
    async with state.db.session(principal.scope) as session:
        unsearchable = await repo.count_other_embedder_documents(session, org, embedder.name)
    return SearchResult(
        question=question,
        results=[
            PassageView(
                document_id=str(h.document_id),
                title=h.title,
                position=h.position,
                text=h.content,
                score=round(h.score, 4),
            )
            for h in hits
        ],
        semantic=embedder.semantic,
        note=None if embedder.semantic else LEXICAL_NOTE,
        unsearchable_documents=unsearchable,
    )


@router.get("/knowledge/search")
async def search(
    principal: Viewer,
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> SearchResult:
    return await search_knowledge(state_of(request), principal, q, limit)
