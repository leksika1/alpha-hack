"""Bank analytics catalog and links to the original PDF pages."""

import uuid
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import iterate_in_threadpool

from app.api.deps import Conn, SettingsDep, User
from app.api.v1.auth import ERR_401
from app.core.errors import ProblemError
from app.core.security import TokenError
from app.services import analytics_docs as svc

router = APIRouter(prefix="/analytics", tags=["analytics"])


class AnalyticsDocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    title: str
    file_name: str
    doc_date: date | None
    topics: list[str]
    pages: int
    chunks: int
    extraction: str | None
    ocr_confidence: float | None
    origin: str


class DownloadOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    title: str
    url: str
    page: int | None
    expires_in: int


@router.get(
    "/documents",
    response_model=list[AnalyticsDocumentOut],
    responses=ERR_401,
    summary="Материалы аналитики Альфа-Банка, доступные сервису",
)
async def list_documents(user: User, conn: Conn) -> list[AnalyticsDocumentOut]:
    return [AnalyticsDocumentOut(**d) for d in await svc.list_documents(conn)]


@router.get(
    "/documents/{document_id}/download",
    response_model=DownloadOut,
    responses={
        **ERR_401,
        404: {"description": "Документ не найден"},
        422: {"description": "Нет такой страницы"},
    },
    summary="Временная ссылка на PDF (15 минут), с переходом к странице `#page=N`",
)
async def download(
    document_id: uuid.UUID,
    user: User,
    conn: Conn,
    settings: SettingsDep,
    page: Annotated[int | None, Query(ge=1)] = None,
) -> DownloadOut:
    try:
        link = await svc.download_link(conn, settings, document_id, page)
    except ValueError as exc:
        raise ProblemError(422, "validation", "Invalid page", str(exc)) from exc
    if link is None:
        raise ProblemError(404, "not-found", "Document not found")
    return DownloadOut(**link)


@router.get(
    "/documents/{document_id}/file",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF"},
        401: {"description": "Ссылка недействительна или устарела"},
        404: {"description": "Документ не найден"},
    },
    summary="PDF по ссылке из `download` и `source-click`: токен в адресе, без заголовка Authorization",
)
async def file(
    document_id: uuid.UUID,
    conn: Conn,
    settings: SettingsDep,
    token: Annotated[str, Query(min_length=1)],
) -> StreamingResponse:
    try:
        doc = await svc.document_for_token(conn, settings, document_id, token)
    except TokenError as exc:
        raise ProblemError(401, "unauthorized", "Invalid or expired link", str(exc)) from exc
    if doc is None:
        raise ProblemError(404, "not-found", "Document not found")
    return StreamingResponse(
        iterate_in_threadpool(svc.iter_pdf(settings, doc["s3_key"])),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(doc['file_name'])}",
            "Cache-Control": "private, max-age=900",
            "X-Content-Type-Options": "nosniff",
        },
    )
