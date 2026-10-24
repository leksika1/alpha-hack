"""Public API v1."""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    admin_sources,
    analytics,
    auth,
    catalog,
    companies,
    engagement,
    feed,
    insights,
    notifications,
    qa,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(companies.router)
router.include_router(feed.router)
router.include_router(insights.router)
router.include_router(catalog.router)
router.include_router(analytics.router)
router.include_router(qa.router)
router.include_router(notifications.router)
router.include_router(engagement.router)
router.include_router(admin.router)
router.include_router(admin_sources.router)
