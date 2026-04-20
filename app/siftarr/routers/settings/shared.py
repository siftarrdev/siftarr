"""Shared router objects for settings handlers."""

import logging

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/siftarr/templates")
logger = logging.getLogger(__name__)
