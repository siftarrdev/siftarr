"""asset_version() must be available to every page that extends base.html."""

from typing import Any, cast

from app.siftarr.config import get_static_version
from app.siftarr.routers import auth_router, dashboard, rules, settings, stats


def test_all_template_instances_expose_asset_version():
    instances = [
        dashboard.templates,
        rules.templates,
        settings.templates,
        stats.templates,
        auth_router._get_templates(),
    ]
    for templates in instances:
        jinja_globals = cast(dict[str, Any], templates.env.globals)
        assert jinja_globals.get("asset_version") is get_static_version
