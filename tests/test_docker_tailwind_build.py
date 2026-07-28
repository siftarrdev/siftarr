"""Guards for the Tailwind build stage in docker/Dockerfile.

Tailwind only emits utilities it can find in scanned sources. The dashboard
generates most of the request-details markup from JS, so if the build stage does
not have those files the container ships a stylesheet missing every JS-only
class -- rows render with no spacing while a local build looks correct.
"""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "docker" / "Dockerfile"


def _tailwind_stage() -> str:
    content = DOCKERFILE.read_text(encoding="utf-8")
    start = content.index("FROM node:")
    end = content.index("FROM python:")
    return content[start:end]


def test_tailwind_stage_scans_the_whole_app_tree():
    stage = _tailwind_stage()

    assert "COPY app/ ./app/" in stage
    # Copying only a subset is what caused the missing-utility bug.
    assert "COPY app/siftarr/templates/" not in stage


def test_tailwind_stage_copies_sources_before_building():
    stage = _tailwind_stage()

    assert stage.index("COPY app/ ./app/") < stage.index("@tailwindcss/cli")


def test_tailwind_output_is_copied_into_the_runtime_image():
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert (
        "COPY --from=tailwind-builder /build/app/siftarr/static/css/tailwind.css"
        " ./app/siftarr/static/css/tailwind.css" in content
    )
