"""Public API v1."""

from fastapi import APIRouter

from app.api.v1 import admin, analytics, auth, catalog, companies, feed, insights

router = APIRouter()
router.include_router(auth.router)
router.include_router(companies.router)
router.include_router(feed.router)
router.include_router(insights.router)
router.include_router(catalog.router)
router.include_router(analytics.router)
router.include_router(admin.router)
