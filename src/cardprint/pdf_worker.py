"""Isolated child-process PyMuPDF worker with coalesced scheduling and bounded caches.

This module intentionally avoids importing PySide6 and avoids importing pymupdf at
module top level. PyMuPDF is imported strictly inside the spawned child process
entry point (`_worker_main`) so parent threads and Qt UI code never load or share
live PyMuPDF objects.
"""

from __future__ import annotations

import atexit
import collections
import concurrent.futures
import multiprocessing
import os
import threading
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

PDF_DPI = 300
MAX_PIXELS = 80_000_000
THUMBNAIL_CACHE_MAX_BYTES = 32 * 1024 * 1024
DISPLAY_CACHE_MAX_BYTES = 128 * 1024 * 1024
DEFAULT_THUMBNAIL_BATCH_SIZE = 8


class RequestCancelledError(Exception):
    """Raised when a queued PDF worker request is cancelled or coalesced."""


class PdfWorkerCrashError(RuntimeError):
    """Raised when the isolated PDF child process terminates unexpectedly."""


class ByteLRUCache:
    """Thread-safe byte-bounded LRU cache."""

    def __init__(self, max_bytes: int):
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative.")
        self.max_bytes = int(max_bytes)
        self._items: collections.OrderedDict[Any, tuple[Any, int]] = collections.OrderedDict()
        self._current_bytes = 0
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return self._current_bytes

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, key: Any) -> Any | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return entry[0]

    def put(self, key: Any, value: Any, nbytes: int) -> bool:
        if nbytes < 0:
            raise ValueError("nbytes must be non-negative.")
        with self._lock:
            existing = self._items.pop(key, None)
            if existing is not None:
                self._current_bytes -= existing[1]
            if nbytes > self.max_bytes:
                return False
            while self._items and self._current_bytes + nbytes > self.max_bytes:
                _, (_, evicted_bytes) = self._items.popitem(last=False)
                self._current_bytes -= evicted_bytes
                self.evictions += 1
            self._items[key] = (value, nbytes)
            self._current_bytes += nbytes
            return True

    def remove(self, key: Any) -> None:
        with self._lock:
            existing = self._items.pop(key, None)
            if existing is not None:
                self._current_bytes -= existing[1]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._current_bytes = 0


@dataclass(frozen=True)
class RenderedImage:
    width: int
    height: int
    rgb_bytes: bytes

    @property
    def nbytes(self) -> int:
        return len(self.rgb_bytes)

    def to_pil(self) -> Image.Image:
        return Image.frombytes("RGB", (self.width, self.height), self.rgb_bytes)

    def to_array(self) -> np.ndarray:
        array = np.frombuffer(self.rgb_bytes, dtype=np.uint8).reshape((self.height, self.width, 3))
        copy = array.copy()
        copy.flags.writeable = False
        return copy


@dataclass(frozen=True)
class PdfInfo:
    path: str
    page_count: int
    page_sizes_pt: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ThumbnailBatchResult:
    path: str
    total_pages: int
    items: tuple[tuple[int, RenderedImage], ...]


@dataclass(frozen=True)
class InspectPdfRequest:
    path: str
    request_id: int = 0
    session_id: int = 0
    revision: int = 0


@dataclass(frozen=True)
class ThumbnailBatchRequest:
    path: str
    start_page: int
    count: int
    max_width: int = 180
    max_height: int = 140
    request_id: int = 0
    session_id: int = 0
    revision: int = 0


@dataclass(frozen=True)
class RenderPageRequest:
    path: str
    page: int
    dpi: float = PDF_DPI
    request_id: int = 0
    session_id: int = 0
    revision: int = 0


@dataclass(frozen=True)
class ValidatePdfRequest:
    path: str
    expected_pages: int | None = None
    request_id: int = 0
    session_id: int = 0
    revision: int = 0


@dataclass(frozen=True)
class ShutdownRequest:
    request_id: int = 0


@dataclass(frozen=True)
class WorkerResponse:
    request_id: int
    ok: bool
    payload: Any = None
    error: str | None = None
    error_type: str = "ValueError"
    worker_pid: int = 0


def _open_pdf_in_worker(pymupdf_mod: Any, path: str):
    try:
        doc = pymupdf_mod.open(path)
    except Exception as exc:
        raise ValueError(
            "Не удалось прочитать PDF: файл повреждён или имеет неверный формат."
        ) from exc
    if doc.needs_pass:
        doc.close()
        raise ValueError("PDF защищён паролем. Откройте локальную копию без парольной защиты.")
    if doc.page_count == 0:
        doc.close()
        raise ValueError("PDF не содержит страниц.")
    return doc


def _validate_page_rect(rect: Any) -> None:
    if not isfinite(rect.width) or not isfinite(rect.height) or rect.width <= 0 or rect.height <= 0:
        raise ValueError("PDF содержит страницу с некорректным размером.")


def _handle_worker_request(pymupdf_mod: Any, req: Any) -> Any:
    if isinstance(req, InspectPdfRequest):
        with _open_pdf_in_worker(pymupdf_mod, req.path) as doc:
            sizes = []
            for page in doc:
                _validate_page_rect(page.rect)
                sizes.append((float(page.rect.width), float(page.rect.height)))
            return PdfInfo(req.path, doc.page_count, tuple(sizes))

    if isinstance(req, ThumbnailBatchRequest):
        if req.start_page < 0 or req.count <= 0:
            raise ValueError("Некорректный диапазон страниц для миниатюр PDF.")
        with _open_pdf_in_worker(pymupdf_mod, req.path) as doc:
            total = doc.page_count
            end_page = min(req.start_page + req.count, total)
            items: list[tuple[int, RenderedImage]] = []
            for page_index in range(req.start_page, end_page):
                page = doc[page_index]
                _validate_page_rect(page.rect)
                scale = min(req.max_width / page.rect.width, req.max_height / page.rect.height)
                pix = page.get_pixmap(
                    matrix=pymupdf_mod.Matrix(scale, scale),
                    colorspace=pymupdf_mod.csRGB,
                    alpha=False,
                )
                items.append(
                    (
                        page_index,
                        RenderedImage(pix.width, pix.height, bytes(pix.samples)),
                    )
                )
            return ThumbnailBatchResult(req.path, total, tuple(items))

    if isinstance(req, RenderPageRequest):
        with _open_pdf_in_worker(pymupdf_mod, req.path) as doc:
            if not (0 <= req.page < doc.page_count):
                raise ValueError("Указанная страница PDF отсутствует.")
            page = doc[req.page]
            _validate_page_rect(page.rect)
            if page.rect.width * page.rect.height * (req.dpi / 72) ** 2 > MAX_PIXELS:
                raise ValueError("Страница слишком велика для рендеринга в 300 DPI (более 80 Мп).")
            pix = page.get_pixmap(dpi=req.dpi, colorspace=pymupdf_mod.csRGB, alpha=False)
            if pix.width * pix.height > MAX_PIXELS:
                raise ValueError("Страница слишком велика для рендеринга в 300 DPI (более 80 Мп).")
            if min(pix.width, pix.height) < 4:
                raise ValueError("Изображение слишком маленькое для выделения карточки.")
            return RenderedImage(pix.width, pix.height, bytes(pix.samples))

    if isinstance(req, ValidatePdfRequest):
        with _open_pdf_in_worker(pymupdf_mod, req.path) as doc:
            if req.expected_pages is not None and doc.page_count != req.expected_pages:
                raise ValueError("Сохраняемый PDF не прошёл проверку количества страниц.")
            sizes = []
            for page in doc:
                _validate_page_rect(page.rect)
                sizes.append((float(page.rect.width), float(page.rect.height)))
            return PdfInfo(req.path, doc.page_count, tuple(sizes))

    raise ValueError("Неизвестный тип запроса к процессу PDF.")


def _worker_main(recv_conn: Any, send_conn: Any) -> None:
    """Spawned child-process entry point owning all PyMuPDF calls."""
    import pymupdf

    pid = os.getpid()
    try:
        while True:
            try:
                req = recv_conn.recv()
            except EOFError:
                break
            if isinstance(req, ShutdownRequest):
                try:
                    send_conn.send(WorkerResponse(req.request_id, True, None, worker_pid=pid))
                except (BrokenPipeError, EOFError, OSError):
                    pass
                break
            try:
                payload = _handle_worker_request(pymupdf, req)
            except Exception as exc:
                response = WorkerResponse(
                    req.request_id,
                    False,
                    error=str(exc) or "Не удалось выполнить операцию с PDF.",
                    error_type=type(exc).__name__,
                    worker_pid=pid,
                )
            else:
                response = WorkerResponse(req.request_id, True, payload, worker_pid=pid)
            try:
                send_conn.send(response)
            except (BrokenPipeError, EOFError, OSError):
                break
    finally:
        for conn in (recv_conn, send_conn):
            try:
                conn.close()
            except OSError:
                pass


@dataclass
class _QueuedItem:
    request: Any
    coalesce_key: Any
    is_export: bool
    future: concurrent.futures.Future
    on_done: Callable[[Any], None] | None = None
    on_error: Callable[[Exception], None] | None = None
    on_cancelled: Callable[[], None] | None = None
    cancelled: bool = False


class PdfRequestHandle:
    """Handle for tracking and cancelling a queued PDF request."""

    def __init__(self, client: "PdfWorkerClient", item: _QueuedItem):
        self._client = client
        self._item = item

    @property
    def request_id(self) -> int:
        return self._item.request.request_id

    @property
    def session_id(self) -> int:
        return getattr(self._item.request, "session_id", 0)

    @property
    def revision(self) -> int:
        return getattr(self._item.request, "revision", 0)

    @property
    def future(self) -> concurrent.futures.Future:
        return self._item.future

    def cancel(self) -> bool:
        return self._client._cancel_item(self._item)

    def result(self, timeout: float | None = None) -> Any:
        return self._item.future.result(timeout=timeout)


class PdfWorkerClient:
    """Client managing one spawned PyMuPDF worker process, coalescing, and byte-bounded caches."""

    def __init__(
        self,
        *,
        thumbnail_cache_bytes: int = THUMBNAIL_CACHE_MAX_BYTES,
        display_cache_bytes: int = DISPLAY_CACHE_MAX_BYTES,
        start_method: str = "spawn",
    ):
        self._ctx = multiprocessing.get_context(start_method)
        self.thumbnail_cache = ByteLRUCache(thumbnail_cache_bytes)
        self.display_cache = ByteLRUCache(display_cache_bytes)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: collections.deque[_QueuedItem] = collections.deque()
        self._active_item: _QueuedItem | None = None
        self._dispatcher: threading.Thread | None = None
        self._process: Any = None
        self._parent_send: Any = None
        self._parent_recv: Any = None
        self._worker_pid: int | None = None
        self._next_request_id = 1
        self._closing = False
        self._closed = False
        self.coalesced_count = 0
        self._pdf_page_counts: dict[tuple[str, int, int], int] = {}

    @property
    def worker_pid(self) -> int | None:
        with self._lock:
            if self._process is not None and self._process.is_alive():
                return self._worker_pid
            return None

    @property
    def is_alive(self) -> bool:
        with self._lock:
            return bool(self._process is not None and self._process.is_alive())

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending) + (1 if self._active_item is not None else 0)

    @staticmethod
    def _file_key(path_str: str) -> tuple[str, int, int] | None:
        try:
            resolved = Path(path_str).resolve()
            st = resolved.stat()
            return (str(resolved), st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _ensure_dispatcher_locked(self) -> None:
        if self._dispatcher is None or not self._dispatcher.is_alive():
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                name="cardprint-pdf-dispatcher",
                daemon=True,
            )
            self._dispatcher.start()

    def _spawn_process_locked(self) -> None:
        parent_recv, child_send = self._ctx.Pipe(duplex=False)
        child_recv, parent_send = self._ctx.Pipe(duplex=False)
        proc = self._ctx.Process(
            target=_worker_main,
            args=(child_recv, child_send),
            name="cardprint-pymupdf-worker",
            daemon=True,
        )
        proc.start()
        # Close child ends in parent so child death immediately raises EOFError on parent_recv.
        child_recv.close()
        child_send.close()
        self._process = proc
        self._parent_send = parent_send
        self._parent_recv = parent_recv
        self._worker_pid = proc.pid

    def _cleanup_process_locked(self) -> None:
        for conn in (self._parent_send, self._parent_recv):
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
        proc = self._process
        self._parent_send = None
        self._parent_recv = None
        self._process = None
        self._worker_pid = None
        if proc is not None:
            try:
                proc.join(timeout=0.2)
            except Exception:
                pass

    def _cancel_item_locked(self, item: _QueuedItem) -> bool:
        if item.cancelled or self._active_item is item:
            return False
        try:
            self._pending.remove(item)
        except ValueError:
            return False
        item.cancelled = True
        return True

    def _finish_cancelled(self, item: _QueuedItem) -> None:
        if not item.future.done():
            item.future.set_exception(RequestCancelledError("Операция отменена."))
        if item.on_cancelled is not None:
            item.on_cancelled()

    def _cancel_item(self, item: _QueuedItem) -> bool:
        with self._lock:
            removed = self._cancel_item_locked(item)
        if removed:
            self._finish_cancelled(item)
        return removed

    def cancel_key(self, coalesce_key: Any) -> int:
        if coalesce_key is None:
            return 0
        cancelled_items: list[_QueuedItem] = []
        with self._lock:
            kept: collections.deque[_QueuedItem] = collections.deque()
            while self._pending:
                item = self._pending.popleft()
                if item.coalesce_key == coalesce_key:
                    item.cancelled = True
                    cancelled_items.append(item)
                else:
                    kept.append(item)
            self._pending = kept
        for item in cancelled_items:
            self._finish_cancelled(item)
        return len(cancelled_items)

    def cancel_session(self, session_id: int) -> int:
        cancelled_items: list[_QueuedItem] = []
        with self._lock:
            kept: collections.deque[_QueuedItem] = collections.deque()
            while self._pending:
                item = self._pending.popleft()
                if getattr(item.request, "session_id", 0) == session_id:
                    item.cancelled = True
                    cancelled_items.append(item)
                else:
                    kept.append(item)
            self._pending = kept
        for item in cancelled_items:
            self._finish_cancelled(item)
        return len(cancelled_items)

    def cancel_pending(self, *, keep_exports: bool = True) -> int:
        cancelled_items: list[_QueuedItem] = []
        with self._lock:
            kept: collections.deque[_QueuedItem] = collections.deque()
            while self._pending:
                item = self._pending.popleft()
                if keep_exports and item.is_export:
                    kept.append(item)
                else:
                    item.cancelled = True
                    cancelled_items.append(item)
            self._pending = kept
        for item in cancelled_items:
            self._finish_cancelled(item)
        return len(cancelled_items)

    def submit(
        self,
        request: Any,
        *,
        coalesce_key: Any = None,
        is_export: bool = False,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
    ) -> PdfRequestHandle:
        coalesced_items: list[_QueuedItem] = []
        with self._lock:
            if self._closing or self._closed:
                req = replace(request, request_id=self._next_request_id)
                self._next_request_id += 1
                item = _QueuedItem(
                    req,
                    coalesce_key,
                    is_export,
                    concurrent.futures.Future(),
                    on_done,
                    on_error,
                    on_cancelled,
                    cancelled=True,
                )
                self._finish_cancelled(item)
                return PdfRequestHandle(self, item)

            req_id = self._next_request_id
            self._next_request_id += 1
            req = replace(request, request_id=req_id)

            if coalesce_key is not None:
                kept: collections.deque[_QueuedItem] = collections.deque()
                while self._pending:
                    queued = self._pending.popleft()
                    if queued.coalesce_key == coalesce_key:
                        queued.cancelled = True
                        coalesced_items.append(queued)
                        self.coalesced_count += 1
                    else:
                        kept.append(queued)
                self._pending = kept

            item = _QueuedItem(
                request=req,
                coalesce_key=coalesce_key,
                is_export=is_export,
                future=concurrent.futures.Future(),
                on_done=on_done,
                on_error=on_error,
                on_cancelled=on_cancelled,
            )
            self._pending.append(item)
            self._ensure_dispatcher_locked()
            self._cond.notify_all()

        for old in coalesced_items:
            self._finish_cancelled(old)
        return PdfRequestHandle(self, item)

    def _try_cache_hit(self, req: Any) -> Any | None:
        if isinstance(req, RenderPageRequest):
            file_key = self._file_key(req.path)
            if file_key is None:
                return None
            return self.display_cache.get((file_key, req.page, float(req.dpi)))

        if isinstance(req, ThumbnailBatchRequest):
            file_key = self._file_key(req.path)
            if file_key is None or req.start_page < 0 or req.count <= 0:
                return None
            with self._lock:
                total = self._pdf_page_counts.get(file_key)
            if total is None:
                return None
            end_page = min(req.start_page + req.count, total)
            if end_page <= req.start_page:
                return ThumbnailBatchResult(req.path, total, ())
            items: list[tuple[int, RenderedImage]] = []
            for page_index in range(req.start_page, end_page):
                cached = self.thumbnail_cache.get(
                    (file_key, page_index, req.max_width, req.max_height)
                )
                if cached is None:
                    return None
                items.append((page_index, cached))
            return ThumbnailBatchResult(req.path, total, tuple(items))

        return None

    def _store_cache(self, req: Any, payload: Any) -> None:
        file_key = self._file_key(req.path)
        if file_key is None:
            return
        if isinstance(req, InspectPdfRequest) and isinstance(payload, PdfInfo):
            with self._lock:
                self._pdf_page_counts[file_key] = payload.page_count
        elif isinstance(req, RenderPageRequest) and isinstance(payload, RenderedImage):
            self.display_cache.put((file_key, req.page, float(req.dpi)), payload, payload.nbytes)
        elif isinstance(req, ThumbnailBatchRequest) and isinstance(payload, ThumbnailBatchResult):
            with self._lock:
                self._pdf_page_counts[file_key] = payload.total_pages
            for page_index, img in payload.items:
                self.thumbnail_cache.put(
                    (file_key, page_index, req.max_width, req.max_height),
                    img,
                    img.nbytes,
                )

    def _execute_in_worker(self, req: Any) -> Any:
        with self._lock:
            if self._process is not None and not self._process.is_alive():
                self._cleanup_process_locked()
                raise PdfWorkerCrashError(
                    "Фоновый процесс обработки PDF аварийно завершился. Повторите операцию."
                )
            if self._process is None:
                self._spawn_process_locked()
            send_conn = self._parent_send
            recv_conn = self._parent_recv

        try:
            send_conn.send(req)
            resp: WorkerResponse = recv_conn.recv()
        except (EOFError, BrokenPipeError, OSError) as exc:
            with self._lock:
                self._cleanup_process_locked()
            raise PdfWorkerCrashError(
                "Фоновый процесс обработки PDF аварийно завершился. Повторите операцию."
            ) from exc

        if not resp.ok:
            msg = resp.error or "Не удалось выполнить операцию с PDF."
            if resp.error_type == "RuntimeError":
                raise RuntimeError(msg)
            raise ValueError(msg)
        return resp.payload

    def _dispatch_loop(self) -> None:
        while True:
            with self._lock:
                while not self._pending and not self._closing:
                    self._cond.wait()
                if not self._pending and self._closing:
                    self._active_item = None
                    self._cond.notify_all()
                    return
                item = self._pending.popleft()
                if item.cancelled:
                    continue
                self._active_item = item

            req = item.request
            try:
                cached = self._try_cache_hit(req)
                if cached is not None:
                    result = cached
                else:
                    result = self._execute_in_worker(req)
                    self._store_cache(req, result)
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
                if item.on_error is not None:
                    item.on_error(exc)
            else:
                if not item.future.done():
                    item.future.set_result(result)
                if item.on_done is not None:
                    item.on_done(result)
            finally:
                with self._lock:
                    self._active_item = None
                    self._cond.notify_all()

    def inspect_pdf(self, path: str | Path) -> PdfInfo:
        resolved = str(Path(path).resolve())
        return self.submit(InspectPdfRequest(resolved)).result()

    def render_thumbnail_batch(
        self,
        path: str | Path,
        start_page: int,
        count: int = DEFAULT_THUMBNAIL_BATCH_SIZE,
        max_width: int = 180,
        max_height: int = 140,
    ) -> ThumbnailBatchResult:
        resolved = str(Path(path).resolve())
        return self.submit(
            ThumbnailBatchRequest(resolved, start_page, count, max_width, max_height)
        ).result()

    def render_page(self, path: str | Path, page: int, dpi: float = PDF_DPI) -> RenderedImage:
        resolved = str(Path(path).resolve())
        return self.submit(RenderPageRequest(resolved, page, dpi=dpi)).result()

    def validate_pdf(self, path: str | Path, expected_pages: int | None = None) -> PdfInfo:
        resolved = str(Path(path).resolve())
        return self.submit(
            ValidatePdfRequest(resolved, expected_pages=expected_pages),
            is_export=True,
        ).result()

    def shutdown(self, *, timeout: float = 5.0) -> None:
        cancelled_items: list[_QueuedItem] = []
        with self._lock:
            if self._closed:
                return
            self._closing = True
            kept: collections.deque[_QueuedItem] = collections.deque()
            while self._pending:
                item = self._pending.popleft()
                if item.is_export:
                    kept.append(item)
                else:
                    item.cancelled = True
                    cancelled_items.append(item)
            self._pending = kept
            self._cond.notify_all()
            dispatcher = self._dispatcher

        for item in cancelled_items:
            self._finish_cancelled(item)

        if dispatcher is not None and dispatcher.is_alive():
            dispatcher.join(timeout=timeout)

        with self._lock:
            proc = self._process
            send_conn = self._parent_send
            recv_conn = self._parent_recv
            self._process = None
            self._parent_send = None
            self._parent_recv = None
            self._worker_pid = None
            self._dispatcher = None
            self._closed = True

        if proc is not None and proc.is_alive():
            try:
                if send_conn is not None:
                    send_conn.send(ShutdownRequest())
                if recv_conn is not None and recv_conn.poll(timeout):
                    recv_conn.recv()
            except (EOFError, BrokenPipeError, OSError):
                pass
            proc.join(timeout=min(timeout, 2.0))
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=0.5)
        elif proc is not None:
            try:
                proc.join(timeout=0.2)
            except Exception:
                pass

        for conn in (send_conn, recv_conn):
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass


_default_lock = threading.Lock()
_default_worker: PdfWorkerClient | None = None


def get_default_pdf_worker() -> PdfWorkerClient:
    """Return the lazily initialized process-wide PDF worker client."""
    global _default_worker
    with _default_lock:
        if _default_worker is None or _default_worker._closed:
            _default_worker = PdfWorkerClient()
        return _default_worker


def shutdown_default_pdf_worker() -> None:
    """Shut down the default PDF worker process if it was started."""
    global _default_worker
    with _default_lock:
        worker = _default_worker
        _default_worker = None
    if worker is not None:
        worker.shutdown()


atexit.register(shutdown_default_pdf_worker)
