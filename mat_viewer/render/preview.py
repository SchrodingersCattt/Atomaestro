"""Cached CPU/GPU image previews and one-viewer activation.

Request parsing lives in ``preview_request``. The raster cache lives in
``preview_cache``. Activation HTML and host pages live in ``preview_host``.
This module re-exports the public names.
"""

from __future__ import annotations

from .preview_cache import PreviewCache, encode_preview_raster, render_preview
from .preview_host import (
    activation_html,
    write_camera_swap_page,
    write_preview_host,
    write_shared_plotlyjs,
)
from .preview_request import (
    DEFAULT_PREVIEW_HEIGHT,
    DEFAULT_PREVIEW_SCALE,
    DEFAULT_PREVIEW_WIDTH,
    MAX_ACTIVE_VIEWERS,
    PREVIEW_CACHE_SCHEMA,
    PREVIEW_IMAGE_SCHEMA,
    RASTER_NOTE,
    TIMING_NOTE,
    PreviewError,
    PreviewImage,
    PreviewNotFound,
    PreviewRequest,
    PreviewUnsupported,
    TextStructure,
    build_preview_request,
    encoder_descriptor,
    json_ready,
    preview_cache_key,
    preview_request_from_payload,
    scene_summary,
    synthetic_ch_pair_grid,
)

__all__ = [
    "DEFAULT_PREVIEW_HEIGHT",
    "DEFAULT_PREVIEW_SCALE",
    "DEFAULT_PREVIEW_WIDTH",
    "MAX_ACTIVE_VIEWERS",
    "PREVIEW_CACHE_SCHEMA",
    "PREVIEW_IMAGE_SCHEMA",
    "RASTER_NOTE",
    "TIMING_NOTE",
    "PreviewCache",
    "PreviewError",
    "PreviewImage",
    "PreviewNotFound",
    "PreviewRequest",
    "PreviewUnsupported",
    "TextStructure",
    "activation_html",
    "build_preview_request",
    "encode_preview_raster",
    "encoder_descriptor",
    "json_ready",
    "preview_cache_key",
    "preview_request_from_payload",
    "render_preview",
    "scene_summary",
    "synthetic_ch_pair_grid",
    "write_camera_swap_page",
    "write_preview_host",
    "write_shared_plotlyjs",
]
