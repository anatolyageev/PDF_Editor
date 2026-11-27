import os
import signal
import subprocess
import sys
import threading

import pymupdf
import pytest

from cardprint.export import Sheet, export_sheet
from cardprint.geometry import Layout, arrange
from cardprint.imaging import discover, discover_pdf_batches, load_source
from cardprint.pdf_worker import (
    ByteLRUCache,
    PdfWorkerClient,
    PdfWorkerCrashError,
    RenderPageRequest,
    RequestCancelledError,
)


def _make_pdf(path, pages: int = 3, width: float = 200, height: float = 120):
    with pymupdf.open() as doc:
        for i in range(pages):
            page = doc.new_page(width=width, height=height)
            page.draw_rect(
                pymupdf.Rect(15, 15, width - 15, height - 15),
                color=(0.1, 0.2, 0.5),
                fill=(0.85, 0.9, 0.95),
            )
            page.insert_text((25, 45), f"PAGE {i + 1}", fontsize=14, color=(0.1, 0.1, 0.1))
        doc.save(path)


def test_byte_lru_cache_evicts_lru_and_respects_budget():
    cache = ByteLRUCache(max_bytes=100)
    assert cache.put("a", b"1" * 40, 40)
    assert cache.put("b", b"2" * 40, 40)
    assert cache.current_bytes == 80
    # Touch "a" so "b" becomes least recently used.
    assert cache.get("a") == b"1" * 40
    assert cache.hits == 1
    assert cache.put("c", b"3" * 40, 40)
    assert cache.current_bytes == 80
    assert cache.evictions == 1
    assert cache.get("b") is None
    assert cache.misses == 1
    assert cache.get("a") == b"1" * 40
    assert cache.get("c") == b"3" * 40
    # Oversized entries are rejected without overflowing max_bytes.
    assert not cache.put("huge", b"x" * 101, 101)
    assert cache.current_bytes == 80
    assert cache.entry_count == 2


def test_worker_process_spawn_lifecycle_and_parent_no_pymupdf_import(tmp_path):
    pdf = tmp_path / "sample.pdf"
    _make_pdf(pdf, pages=2)

    client = PdfWorkerClient()
    try:
        info = client.inspect_pdf(pdf)
        assert info.page_count == 2
        assert client.is_alive
        assert client.worker_pid is not None and client.worker_pid != os.getpid()
        img = client.render_page(pdf, 0, dpi=150)
        assert img.width > 0 and img.height > 0
        arr = img.to_array()
        assert not arr.flags.writeable
    finally:
        client.shutdown()
    assert not client.is_alive

    # Verify in a clean subprocess that importing imaging/pdf_worker and running
    # discover() + load_source() never imports pymupdf into the parent process.
    code = (
        "import sys; "
        "from cardprint.imaging import discover, load_source; "
        "from cardprint.pdf_worker import shutdown_default_pdf_worker; "
        f"sources = discover({str(pdf)!r}); "
        "arr = load_source(sources[0]); "
        "assert len(sources) == 2 and arr.shape[2] == 3; "
        "assert 'pymupdf' not in sys.modules and 'fitz' not in sys.modules, list(sys.modules); "
        "shutdown_default_pdf_worker()"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_worker_coalesces_queued_renders_and_cancels_obsolete(tmp_path):
    pdf = tmp_path / "multi.pdf"
    _make_pdf(pdf, pages=5)

    client = PdfWorkerClient()
    gate = threading.Event()
    try:
        # Hold the dispatcher thread briefly with a blocking gate via an in-flight item.
        first_started = threading.Event()
        orig_exec = client._execute_in_worker

        def blocking_exec(req):
            if getattr(req, "page", None) == 0:
                first_started.set()
                gate.wait(timeout=5)
            return orig_exec(req)

        client._execute_in_worker = blocking_exec  # type: ignore[method-assign]

        h0 = client.submit(
            RenderPageRequest(str(pdf), 0, dpi=120, session_id=1, revision=1),
            coalesce_key=("side", "front"),
        )
        assert first_started.wait(timeout=5)
        h1 = client.submit(
            RenderPageRequest(str(pdf), 1, dpi=120, session_id=1, revision=2),
            coalesce_key=("side", "front"),
        )
        h2 = client.submit(
            RenderPageRequest(str(pdf), 2, dpi=120, session_id=1, revision=3),
            coalesce_key=("side", "front"),
        )
        h3 = client.submit(
            RenderPageRequest(str(pdf), 3, dpi=120, session_id=1, revision=4),
            coalesce_key=("side", "front"),
        )
        gate.set()

        assert h0.result(timeout=10).width > 0
        for stale_handle in (h1, h2):
            with pytest.raises(RequestCancelledError):
                stale_handle.result(timeout=5)
        assert h3.result(timeout=10).width > 0
        assert client.coalesced_count >= 2
    finally:
        gate.set()
        client.shutdown()


def test_worker_thumbnail_batching_display_cache_and_pixel_limits(tmp_path):
    pdf = tmp_path / "twelve.pdf"
    _make_pdf(pdf, pages=12, width=180, height=110)

    # Small byte budgets force deterministic LRU eviction during the test.
    client = PdfWorkerClient(thumbnail_cache_bytes=220_000, display_cache_bytes=450_000)
    try:
        batches = list(discover_pdf_batches(pdf, batch_size=4, worker=client))
        assert [len(b[0]) for b in batches] == [4, 4, 4]
        assert all(total == 12 for _, total in batches)
        assert client.thumbnail_cache.current_bytes <= 220_000

        # Repeat page render hits display cache when fitting within budget.
        img0 = client.render_page(pdf, 0, dpi=120)
        hits_before = client.display_cache.hits
        img0_again = client.render_page(pdf, 0, dpi=120)
        assert img0_again.rgb_bytes == img0.rgb_bytes
        assert client.display_cache.hits == hits_before + 1

        # Render several more pages to force eviction within 450 KiB.
        for p in range(1, 4):
            client.render_page(pdf, p, dpi=120)
        assert client.display_cache.current_bytes <= 450_000
        assert client.display_cache.evictions >= 1

        # Oversized PDF page (> 80 MP at 300 DPI) fails clearly.
        huge_pdf = tmp_path / "huge.pdf"
        _make_pdf(huge_pdf, pages=1, width=3000, height=3000)
        with pytest.raises(ValueError, match="80 Мп"):
            load_source(discover(huge_pdf, worker=client)[0], worker=client)
    finally:
        client.shutdown()


def test_worker_crash_recovery(tmp_path):
    pdf = tmp_path / "crash.pdf"
    _make_pdf(pdf, pages=2)
    client = PdfWorkerClient()
    try:
        info = client.inspect_pdf(pdf)
        assert info.page_count == 2
        pid1 = client.worker_pid
        assert pid1 is not None

        os.kill(pid1, signal.SIGKILL)
        os.waitpid(pid1, 0)

        with pytest.raises(PdfWorkerCrashError, match="аварийно"):
            client.render_page(pdf, 0, dpi=120)

        # Next operation spawns a fresh worker process and succeeds.
        img = client.render_page(pdf, 0, dpi=120)
        assert img.width > 0
        pid2 = client.worker_pid
        assert pid2 is not None and pid2 != pid1
    finally:
        client.shutdown()


def test_export_validates_staged_pdf_and_blocks_corrupt_publish(tmp_path, monkeypatch):
    from PIL import Image

    import cardprint.export as exporting

    image = Image.new("RGB", (100, 60), "blue")
    sheet = Sheet({"front": image}, arrange({"front": (85.6, 53.98)}, Layout()))
    target = tmp_path / "corrupt.pdf"

    def write_corrupt(_sheet, staging_path):
        staging_path.write_bytes(b"%PDF-broken-payload")

    monkeypatch.setattr(exporting, "_write_pdf", write_corrupt)
    client = PdfWorkerClient()
    try:
        with pytest.raises(ValueError, match="повреждён"):
            export_sheet(sheet, target, "pdf", pdf_worker=client)
        assert not target.exists()
        assert not list(tmp_path.glob(".cardprint-*"))
    finally:
        client.shutdown()
