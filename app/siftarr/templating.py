"""Shared Jinja2 template configuration.

Every router builds its own ``Jinja2Templates`` instance; this module is the one
place that teaches them about globals shared by ``base.html``.

``asset_version()`` is exposed as a callable (not a plain value) so it is
evaluated per render. That matters in dev, where ``CACHE_STATIC_ASSETS=false``
makes :func:`get_static_version` return a timestamp — a value captured at import
time would freeze the cache-busting query string for the life of the process.
"""

from typing import Any, cast

from fastapi.templating import Jinja2Templates

from app.siftarr.config import get_static_version


def configure_templates(templates: Jinja2Templates) -> Jinja2Templates:
    """Register shared template globals and return the same instance."""
    jinja_globals = cast(dict[str, Any], templates.env.globals)
    jinja_globals["asset_version"] = get_static_version
    return templates
