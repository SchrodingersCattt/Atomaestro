"""Server-rendered structure previews.

``POST /api/v2/preview`` accepts a structure, style, camera, and pixel
size and returns a cached PNG or WebP. ``GET /api/v2/preview/activate/<ticket>``
returns one Plotly document for that same plan. See
``docs/agents/preview_api.md``.
"""

from __future__ import annotations

from flask import Response, jsonify, request

from ..render.gpu_errors import GPUUnavailableError, GPUUnsupportedError
from ..render.preview import (
    PreviewCache,
    PreviewError,
    PreviewNotFound,
    activation_html,
    preview_request_from_payload,
)


def register_preview_routes(v2, backend) -> dict:
    """Register preview routes on the v2 blueprint.

    ``backend`` is unused. The preview cache is independent of the Dash
    scene so a host can render a structure that is not the open tab.
    One cache lives for the life of this process.
    """

    del backend
    cache = PreviewCache()

    def _headers(image) -> dict[str, str]:
        headers = {
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-MatterVis-Cache-Key": image.cache_key,
            "X-MatterVis-Cache-Status": image.cache_status,
            "X-MatterVis-Plan-Sha256": image.plan_sha256,
            "X-MatterVis-Rasterized": "1" if image.rasterized else "0",
        }
        if image.ticket:
            headers["X-MatterVis-Ticket"] = image.ticket
        return headers

    def _failure(exc: Exception):
        if isinstance(exc, PreviewNotFound):
            return _error(exc, 404)
        if isinstance(exc, GPUUnavailableError):
            return _error(
                exc,
                503,
                hint=(
                    "Select backend cpu, or provide a usable WebGPU adapter. "
                    "The preview did not fall back to CPU."
                ),
            )
        if isinstance(exc, GPUUnsupportedError):
            return _error(
                exc,
                400,
                hint="The GPU preview rejects this plan and does not fall back to CPU.",
            )
        if isinstance(exc, PreviewError):
            return _error(exc, 400, hint=getattr(exc, "hint", None))
        if isinstance(exc, (TypeError, ValueError)):
            return _error(exc, 400)
        return _error(exc, 500)

    def _error(exc: Exception, status: int, *, hint: str | None = None):
        from .shared import _error_response

        return _error_response(exc, status, hint=hint)

    def _wants_image(image_format: str) -> bool:
        accept = request.headers.get("Accept") or ""
        if "application/json" in accept:
            return False
        return f"image/{image_format}" in accept or "image/*" in accept

    @v2.post("/preview")
    def create_structure_preview():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            image = cache.render(preview_request_from_payload(payload))
        except (
            PreviewError,
            GPUUnavailableError,
            GPUUnsupportedError,
            TypeError,
            ValueError,
            OSError,
        ) as exc:
            return _failure(exc)
        if _wants_image(image.image_format):
            return Response(
                image.data,
                mimetype=image.media_type,
                headers=_headers(image),
            )
        return jsonify(image.public_dict())

    @v2.get("/preview/image/<ticket>")
    def structure_preview_image(ticket: str):
        try:
            entry = cache.redeem(ticket)
        except (PreviewError, TypeError, ValueError) as exc:
            return _failure(exc)
        media = "image/png" if entry.image_format == "png" else "image/webp"
        return Response(
            entry.data,
            mimetype=media,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-MatterVis-Cache-Key": entry.request_key,
                "X-MatterVis-Plan-Sha256": entry.plan_sha256,
                "X-MatterVis-Ticket": entry.ticket,
            },
        )

    def _activation_response(ticket: str):
        try:
            entry = cache.redeem(ticket)
            html = activation_html(cache, ticket)
        except (
            PreviewError,
            GPUUnavailableError,
            GPUUnsupportedError,
            TypeError,
            ValueError,
            OSError,
        ) as exc:
            return _failure(exc)
        return Response(
            html,
            mimetype="text/html",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-MatterVis-Plan-Sha256": entry.plan_sha256,
                "X-MatterVis-Cache-Key": entry.request_key,
                "X-MatterVis-Ticket": entry.ticket,
            },
        )

    @v2.get("/preview/activate/<ticket>")
    def activate_structure_preview(ticket: str):
        return _activation_response(ticket)

    @v2.post("/preview/activate")
    def activate_structure_preview_post():
        payload = request.get_json(force=True, silent=True) or {}
        ticket = payload.get("ticket")
        if not ticket:
            return _error(
                PreviewError("ticket is required"),
                400,
                hint="POST /api/v2/preview and open activation.path.",
            )
        return _activation_response(str(ticket))

    return locals()


__all__ = ["register_preview_routes"]
