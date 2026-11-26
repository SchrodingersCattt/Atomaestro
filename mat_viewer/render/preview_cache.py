"""In-process preview raster cache.

The byte budget counts the image and the retained replay, including the
structure text. A namespace partition does not share entries.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any

from .contracts import RenderPlan
from .preview_request import (
    PREVIEW_CACHE_MAX_BYTES,
    PREVIEW_CACHE_MAX_ENTRIES,
    PREVIEW_IMAGE_SCHEMA,
    RASTER_NOTE,
    WEBP_LOSSLESS,
    WEBP_METHOD,
    WEBP_QUALITY,
    PreviewError,
    PreviewImage,
    PreviewNotFound,
    PreviewRequest,
    PreviewUnsupported,
    _source_material,
    build_preview_request,
    camera_payload,
    plan_cache_material,
    preview_cache_key,
    scene_summary,
)


def encode_preview_raster(
    plan: RenderPlan,
    *,
    backend: str,
    image_format: str,
) -> bytes:
    """Rasterize ``plan``. GPU failures propagate and do not use the CPU."""

    if backend == "cpu":
        from .cpu import render_png

        result = render_png(plan, None)
        if not result.data:
            raise PreviewError("CPU preview did not return image bytes")
        if image_format == "png":
            return result.data
        return _png_bytes_to_webp(result.data)
    if backend == "gpu":
        from .gpu import render as render_gpu

        result = render_gpu(plan, None)
        if not result.data:
            raise PreviewError("GPU preview did not return image bytes")
        return result.data
    raise PreviewUnsupported(f"unsupported preview backend {backend!r}")


def _png_bytes_to_webp(data: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image, features

    if not features.check("webp"):
        raise PreviewUnsupported(
            "This Pillow build cannot encode WebP.",
            hint="Request format png, or install a Pillow build with WebP.",
        )
    image = Image.open(BytesIO(data))
    buffer = BytesIO()
    image.save(
        buffer,
        format="WEBP",
        quality=WEBP_QUALITY,
        method=WEBP_METHOD,
        lossless=WEBP_LOSSLESS,
    )
    return buffer.getvalue()


def _media_type(image_format: str) -> str:
    if image_format == "png":
        return "image/png"
    if image_format == "webp":
        return "image/webp"
    raise PreviewUnsupported(f"unsupported preview format {image_format!r}")


@dataclass
class _Entry:
    namespace: str
    request_key: str
    plan_sha256: str
    backend: str
    image_format: str
    data: bytes
    width: int
    height: int
    camera: dict[str, Any]
    summary: dict[str, Any]
    warnings: tuple[str, ...]
    ticket: str
    replay: dict[str, Any]
    live_source: Any
    nbytes: int


@dataclass
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    result: PreviewImage | None = None
    error: BaseException | None = None


class PreviewCache:
    """Namespace-partitioned raster cache with a byte and entry ceiling.

    ``max_bytes`` counts each retained image plus its replay, including
    structure text, and a live source object when one is kept. An entry
    over that budget is returned and not retained. Eviction drops the
    oldest retained entry until both limits hold, and drops its ticket.
    """

    def __init__(
        self,
        *,
        max_entries: int = PREVIEW_CACHE_MAX_ENTRIES,
        max_bytes: int = PREVIEW_CACHE_MAX_BYTES,
    ) -> None:
        if max_entries <= 0 or max_bytes <= 0:
            raise PreviewError("preview cache limits must be positive")
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], _Entry] = OrderedDict()
        self._tickets: dict[str, tuple[str, str]] = {}
        self._plan_index: dict[tuple[str, str, str, str], str] = {}
        self._inflight: dict[tuple[str, str], _Flight] = {}
        self._bytes = 0

    def render(self, request: PreviewRequest) -> PreviewImage:
        material = request.cache_material()
        if material is None:
            return self._render_live(request)
        request_key = preview_cache_key(material)
        started = time.perf_counter()
        cached = self._lookup(request.namespace, request_key, started)
        if cached is not None:
            return cached
        flight, role = self._join(request.namespace, request_key)
        if role == "cached":
            return self._from_entry(flight, started, status="hit", rasterized=False)  # type: ignore[arg-type]
        if role == "wait":
            return self._wait(flight, started)  # type: ignore[arg-type]
        try:
            image = self._miss(request, request_key)
            flight.result = image  # type: ignore[union-attr]
            return image
        except BaseException as exc:
            flight.error = exc  # type: ignore[union-attr]
            raise
        finally:
            self._finish(request.namespace, request_key, flight)  # type: ignore[arg-type]

    def redeem(self, ticket: str) -> _Entry:
        with self._lock:
            key = self._tickets.get(ticket)
            if key is None:
                raise PreviewNotFound(f"unknown preview ticket {ticket!r}")
            entry = self._entries.get(key)
            if entry is None:
                raise PreviewNotFound(f"unknown preview ticket {ticket!r}")
            self._entries.move_to_end(key)
            return entry

    def _lookup(
        self,
        namespace: str,
        request_key: str,
        started: float,
    ) -> PreviewImage | None:
        with self._lock:
            entry = self._entries.get((namespace, request_key))
            if entry is None:
                return None
            self._entries.move_to_end((namespace, request_key))
            return self._image_from_entry(
                entry,
                status="hit",
                rasterized=False,
                timing=_timing(started, prepare=0.0, render_encode=0.0),
            )

    def _join(
        self,
        namespace: str,
        request_key: str,
    ) -> tuple[_Flight | _Entry, str]:
        with self._lock:
            existing = self._entries.get((namespace, request_key))
            if existing is not None:
                self._entries.move_to_end((namespace, request_key))
                return existing, "cached"
            flight = self._inflight.get((namespace, request_key))
            if flight is not None:
                return flight, "wait"
            flight = _Flight()
            self._inflight[(namespace, request_key)] = flight
            return flight, "owner"

    def _wait(self, flight: _Flight, started: float) -> PreviewImage:
        flight.event.wait()
        if flight.error is not None:
            raise flight.error
        if flight.result is None:
            raise PreviewError("in-flight preview finished without an image")
        elapsed = time.perf_counter() - started
        timing = dict(flight.result.timing_s)
        timing["waited"] = elapsed
        timing["total"] = elapsed
        timing["render_encode"] = 0.0
        timing["prepare"] = 0.0
        return replace(
            flight.result,
            cache_status="coalesced",
            rasterized=False,
            timing_s=timing,
        )

    def _finish(self, namespace: str, request_key: str, flight: _Flight) -> None:
        flight.event.set()
        with self._lock:
            current = self._inflight.get((namespace, request_key))
            if current is flight:
                self._inflight.pop((namespace, request_key), None)

    def _miss(
        self,
        request: PreviewRequest,
        request_key: str,
    ) -> PreviewImage:
        prepared = time.perf_counter()
        plan = request.prepare()
        prepare_s = time.perf_counter() - prepared
        alias = self._aliased_bytes(request, plan)
        if alias is not None:
            data, _entry = alias
            return self._remember(
                request,
                request_key,
                plan,
                data,
                status="hit",
                rasterized=False,
                prepare_s=prepare_s,
                render_encode_s=0.0,
            )
        rendered = time.perf_counter()
        data = encode_preview_raster(
            plan,
            backend=request.backend,
            image_format=request.image_format,
        )
        render_s = time.perf_counter() - rendered
        return self._remember(
            request,
            request_key,
            plan,
            data,
            status="miss",
            rasterized=True,
            prepare_s=prepare_s,
            render_encode_s=render_s,
        )

    def _render_live(self, request: PreviewRequest) -> PreviewImage:
        prepared = time.perf_counter()
        plan = request.prepare()
        prepare_s = time.perf_counter() - prepared
        material = plan_cache_material(
            plan,
            backend=request.backend,
            image_format=request.image_format,
        )
        request_key = preview_cache_key(material)
        started = time.perf_counter() - prepare_s
        cached = self._lookup(request.namespace, request_key, started)
        if cached is not None:
            timing = dict(cached.timing_s)
            timing["prepare"] = prepare_s
            timing["total"] = timing["total"] + prepare_s
            return replace(cached, timing_s=timing)
        rendered = time.perf_counter()
        data = encode_preview_raster(
            plan,
            backend=request.backend,
            image_format=request.image_format,
        )
        render_s = time.perf_counter() - rendered
        return self._remember(
            request,
            request_key,
            plan,
            data,
            status="miss",
            rasterized=True,
            prepare_s=prepare_s,
            render_encode_s=render_s,
        )

    def _aliased_bytes(
        self,
        request: PreviewRequest,
        plan: RenderPlan,
    ) -> tuple[bytes, _Entry] | None:
        index = (
            request.namespace,
            plan.fingerprint(),
            request.backend,
            request.image_format,
        )
        with self._lock:
            request_key = self._plan_index.get(index)
            if request_key is None:
                return None
            entry = self._entries.get((request.namespace, request_key))
            if entry is None:
                self._plan_index.pop(index, None)
                return None
            return entry.data, entry

    def _remember(
        self,
        request: PreviewRequest,
        request_key: str,
        plan: RenderPlan,
        data: bytes,
        *,
        status: str,
        rasterized: bool,
        prepare_s: float,
        render_encode_s: float,
    ) -> PreviewImage:
        warnings = tuple(dict.fromkeys((*plan.warnings, RASTER_NOTE)))
        camera = camera_payload(plan)
        summary = scene_summary(plan)
        timing = {
            "prepare": prepare_s,
            "render_encode": render_encode_s,
            "total": prepare_s + render_encode_s,
        }
        source_material = _source_material(request.source)
        replay = {
            "kind": "live" if source_material is None else "json",
            "source": source_material,
            "request": request.request_record(),
        }
        retained_live = request.source if replay["kind"] == "live" else None
        entry = _Entry(
            namespace=request.namespace,
            request_key=request_key,
            plan_sha256=plan.fingerprint(),
            backend=request.backend,
            image_format=request.image_format,
            data=data,
            width=plan.width,
            height=plan.height,
            camera=camera,
            summary=summary,
            warnings=warnings,
            ticket=secrets.token_urlsafe(32),
            replay=replay,
            live_source=retained_live,
            nbytes=_retained_nbytes(data, replay, retained_live),
        )
        retained = self._store(entry)
        return self._image_from_entry(
            entry,
            status=status,
            rasterized=rasterized,
            timing=timing,
            retained=retained,
        )

    def _store(self, entry: _Entry) -> bool:
        with self._lock:
            if entry.nbytes > self.max_bytes:
                return False
            key = (entry.namespace, entry.request_key)
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous.nbytes
                self._tickets.pop(previous.ticket, None)
            self._entries[key] = entry
            self._tickets[entry.ticket] = key
            self._bytes += entry.nbytes
            self._plan_index[
                (
                    entry.namespace,
                    entry.plan_sha256,
                    entry.backend,
                    entry.image_format,
                )
            ] = entry.request_key
            self._evict()
            return key in self._entries

    def _evict(self) -> None:
        while self._entries and (
            len(self._entries) > self.max_entries or self._bytes > self.max_bytes
        ):
            _key, entry = self._entries.popitem(last=False)
            self._bytes -= entry.nbytes
            self._tickets.pop(entry.ticket, None)
            index = (
                entry.namespace,
                entry.plan_sha256,
                entry.backend,
                entry.image_format,
            )
            if self._plan_index.get(index) == entry.request_key:
                self._plan_index.pop(index, None)
                for (namespace, request_key), other in reversed(self._entries.items()):
                    if (
                        namespace == entry.namespace
                        and other.plan_sha256 == entry.plan_sha256
                        and other.backend == entry.backend
                        and other.image_format == entry.image_format
                    ):
                        self._plan_index[index] = request_key
                        break

    def _from_entry(
        self,
        entry: _Entry,
        started: float,
        *,
        status: str,
        rasterized: bool,
    ) -> PreviewImage:
        return self._image_from_entry(
            entry,
            status=status,
            rasterized=rasterized,
            timing=_timing(started, prepare=0.0, render_encode=0.0),
        )

    def _image_from_entry(
        self,
        entry: _Entry,
        *,
        status: str,
        rasterized: bool,
        timing: dict[str, float],
        retained: bool = True,
    ) -> PreviewImage:
        return PreviewImage(
            schema=PREVIEW_IMAGE_SCHEMA,
            media_type=_media_type(entry.image_format),
            image_format=entry.image_format,
            backend=entry.backend,
            width=entry.width,
            height=entry.height,
            data=entry.data,
            sha256=sha256(entry.data).hexdigest(),
            cache_key=entry.request_key,
            cache_status=status,
            rasterized=rasterized,
            retained=retained,
            ticket=entry.ticket if retained else None,
            plan_sha256=entry.plan_sha256,
            camera=entry.camera,
            summary=entry.summary,
            warnings=entry.warnings,
            timing_s=timing,
            namespace=entry.namespace,
        )


def _retained_nbytes(data: bytes, replay: Mapping[str, Any], live_source: Any) -> int:
    """Bytes the cache keeps: image, replay JSON, and a live source object.

    The replay JSON includes the structure text. Omitting it would let a
    small PNG pin a large CIF while the 64 MiB ceiling still looked free.
    """

    encoded = json.dumps(
        replay,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return len(data) + len(encoded) + _live_nbytes(live_source)


def _live_nbytes(source: Any) -> int:
    if source is None:
        return 0
    from .retention import payload_nbytes

    return payload_nbytes(source)


def _timing(
    started: float, *, prepare: float, render_encode: float
) -> dict[str, float]:
    return {
        "prepare": prepare,
        "render_encode": render_encode,
        "total": time.perf_counter() - started,
    }


def render_preview(
    source: Any,
    *,
    cache: PreviewCache,
    namespace: str,
    camera: Any = None,
    view: Any = None,
    style: Any = None,
    width: int | None = None,
    height: int | None = None,
    scale: int | None = None,
    backend: str = "cpu",
    image_format: str = "png",
    atom_groups: Any = None,
    bond_groups: Any = None,
    cell_overlays: Any = None,
    vector_overlays: Any = None,
    topology_data: Any = None,
    atom_property_color: Any = None,
) -> PreviewImage:
    """Render or reuse a namespace-scoped preview raster."""

    request = build_preview_request(
        source,
        namespace=namespace,
        camera=camera,
        view=view,
        style=style,
        width=width,
        height=height,
        scale=scale,
        backend=backend,
        image_format=image_format,
        atom_groups=atom_groups,
        bond_groups=bond_groups,
        cell_overlays=cell_overlays,
        vector_overlays=vector_overlays,
        topology_data=topology_data,
        atom_property_color=atom_property_color,
    )
    return cache.render(request)
