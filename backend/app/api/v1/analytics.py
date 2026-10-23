"""Bank analytics catalog and links to the original PDF pages."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import Conn, SettingsDep, User
from app.api.v1.auth import ERR_401
from app.core.errors import ProblemError
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
    summary="Временная ссылка на PDF (presigned S3), с переходом к странице `#page=N`",
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
