"""Главный роутер — все хендлеры в правильном порядке."""

from aiogram import Router

from .admin import router as admin_router
from .menu import router as menu_router
from .core import router as core_router
from .info import router as info_router
from .features import router as features_router
from .misc import router as misc_router

router = Router()
router.include_router(admin_router)
router.include_router(menu_router)
router.include_router(core_router)
router.include_router(info_router)
router.include_router(features_router)
router.include_router(misc_router)
