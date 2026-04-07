"""Router for rules management pages."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.models.rule import Rule, RuleType
from app.siftarr.services.prowlarr_service import ProwlarrRelease
from app.siftarr.services.rule_engine import RuleEngine
from app.siftarr.services.rule_service import RuleService

router = APIRouter(prefix="/rules", tags=["rules"])
templates = Jinja2Templates(directory="app/siftarr/templates")


def _validate_rule_input(
    rule_type: RuleType,
    pattern: str,
    min_size_gb: float | None,
    max_size_gb: float | None,
) -> None:
    """Validate rule input based on rule type."""
    if rule_type == RuleType.SIZE_LIMIT:
        if min_size_gb is None and max_size_gb is None:
            raise HTTPException(
                status_code=400,
                detail="Size limit rules need a minimum or maximum size.",
            )
        if min_size_gb is not None and min_size_gb < 0:
            raise HTTPException(status_code=400, detail="Minimum size cannot be negative.")
        if max_size_gb is not None and max_size_gb < 0:
            raise HTTPException(status_code=400, detail="Maximum size cannot be negative.")
        if min_size_gb is not None and max_size_gb is not None and min_size_gb > max_size_gb:
            raise HTTPException(
                status_code=400,
                detail="Minimum size cannot be greater than maximum size.",
            )
        return

    try:
        re.compile(pattern)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {e}") from e


@router.get("")
async def list_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """List all rules grouped by type."""
    rule_service = RuleService(db)
    await rule_service.ensure_default_rules()

    exclusions = await rule_service.get_all_rules_by_type(RuleType.EXCLUSION)
    requirements = await rule_service.get_all_rules_by_type(RuleType.REQUIREMENT)
    scorers = await rule_service.get_all_rules_by_type(RuleType.SCORER)
    size_limits = await rule_service.get_all_rules_by_type(RuleType.SIZE_LIMIT)

    return templates.TemplateResponse(
        request,
        "rules.html",
        {
            "request": request,
            "exclusion_rules": exclusions,
            "requirement_rules": requirements,
            "scorer_rules": scorers,
            "size_limit_rules": size_limits,
        },
    )


@router.get("/new")
async def new_rule_form(
    request: Request,
    rule_type: Annotated[str | None, Query(alias="type")] = None,
) -> HTMLResponse:
    """Show form to create a new rule."""
    return templates.TemplateResponse(
        request,
        "rule_form.html",
        {
            "request": request,
            "rule": None,
            "action": "/rules",
            "default_type": rule_type,
        },
    )


@router.post("")
async def create_rule(
    request: Request,
    name: str = Form(...),
    rule_type: str = Form(...),
    media_scope: str = Form("both"),
    pattern: str = Form(...),
    score: int = Form(0),
    min_size_gb: float | None = Form(None),
    max_size_gb: float | None = Form(None),
    description: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Create a new rule."""
    parsed_rule_type = RuleType(rule_type)
    _validate_rule_input(parsed_rule_type, pattern, min_size_gb, max_size_gb)

    rule_service = RuleService(db)
    await rule_service.create_rule(
        name=name,
        rule_type=parsed_rule_type,
        media_scope=media_scope,
        pattern=pattern,
        score=score,
        min_size_gb=min_size_gb,
        max_size_gb=max_size_gb,
        description=description,
    )

    return RedirectResponse(url="/rules", status_code=303)


@router.get("/{rule_id}/edit")
async def edit_rule_form(
    request: Request,
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show form to edit a rule."""
    rule_service = RuleService(db)
    rule = await rule_service.get_rule_by_id(rule_id)

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    return templates.TemplateResponse(
        request,
        "rule_form.html",
        {
            "request": request,
            "rule": rule,
            "action": f"/rules/{rule_id}",
        },
    )


@router.post("/{rule_id}")
async def update_rule(
    request: Request,
    rule_id: int,
    name: str = Form(...),
    media_scope: str = Form("both"),
    pattern: str = Form(...),
    score: int = Form(0),
    min_size_gb: float | None = Form(None),
    max_size_gb: float | None = Form(None),
    description: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Update an existing rule."""
    rule_service = RuleService(db)
    existing_rule = await rule_service.get_rule_by_id(rule_id)
    if not existing_rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    _validate_rule_input(existing_rule.rule_type, pattern, min_size_gb, max_size_gb)

    await rule_service.update_rule(
        rule_id=rule_id,
        name=name,
        media_scope=media_scope,
        pattern=pattern,
        score=score,
        min_size_gb=min_size_gb,
        max_size_gb=max_size_gb,
        description=description,
    )

    return RedirectResponse(url="/rules", status_code=303)


@router.post("/{rule_id}/toggle")
async def toggle_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Toggle a rule's enabled status."""
    rule_service = RuleService(db)
    rule = await rule_service.toggle_rule(rule_id)

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    return RedirectResponse(url="/rules", status_code=303)


@router.post("/{rule_id}/delete")
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Delete a rule."""
    rule_service = RuleService(db)
    deleted = await rule_service.delete_rule(rule_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found")

    return RedirectResponse(url="/rules", status_code=303)


@router.post("/test")
async def test_rule(
    request: Request,
    title: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Test a release title against all rules."""
    from sqlalchemy import select

    rule_service = RuleService(db)
    await rule_service.ensure_default_rules()

    result = await db.execute(select(Rule))
    rules = list(result.scalars().all())

    engine = RuleEngine.from_db_rules(rules=rules)

    # Create a mock release for testing
    mock_release = ProwlarrRelease(
        title=title,
        size=0,
        seeders=0,
        leechers=0,
        download_url="",
        indexer="test",
    )

    evaluation = engine.evaluate(mock_release)

    # Re-render the rules page with test results
    rule_service = RuleService(db)
    exclusions = await rule_service.get_all_rules_by_type(RuleType.EXCLUSION)
    requirements = await rule_service.get_all_rules_by_type(RuleType.REQUIREMENT)
    scorers = await rule_service.get_all_rules_by_type(RuleType.SCORER)
    size_limits = await rule_service.get_all_rules_by_type(RuleType.SIZE_LIMIT)

    return templates.TemplateResponse(
        request,
        "rules.html",
        {
            "request": request,
            "exclusion_rules": exclusions,
            "requirement_rules": requirements,
            "scorer_rules": scorers,
            "size_limit_rules": size_limits,
            "test_result": {
                "passed": evaluation.passed,
                "rejection_reason": evaluation.rejection_reason,
                "total_score": evaluation.total_score,
                "matched_rules": [m for m in evaluation.matches if m.matched],
            },
        },
    )
