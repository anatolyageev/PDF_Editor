"""Shared millimetre placements, safe atomic file writing, and original protection."""

import errno
import hashlib
import os
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .geometry import A4_HEIGHT_MM, A4_WIDTH_MM, MM_TO_PT, Placement
from .pdf_worker import PdfWorkerClient, get_default_pdf_worker

PRINT_HINT = "Для сохранения размера печатайте в масштабе 100% / Фактический размер"

_DEST_LOCKS_GUARD = threading.Lock()
_DEST_LOCKS: dict[str, threading.Lock] = {}


class OriginalOverwriteError(PermissionError):
    """Raised when an export or save attempts to overwrite a protected original/source."""


class SourceChangedError(ValueError):
    """Raised when a referenced source file changed or vanished after import."""


@dataclass(frozen=True)
class SourceFingerprint:
    path: Path
    size: int
    mtime_ns: int
    sha256: str
    device: int | None = None
    inode: int | None = None

    def matches_current(self, path: str | Path | None = None) -> bool:
        target = Path(path) if path is not None else self.path
        try:
            resolved = target.resolve()
            st = resolved.stat()
        except OSError:
            return False
        if st.st_size != self.size:
            return False
        try:
            digest = _sha256_file(resolved)
        except OSError:
            return False
        return digest == self.sha256


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def fingerprint_file(path: str | Path) -> SourceFingerprint:
    try:
        resolved = Path(path).resolve()
        st = resolved.stat()
        digest = _sha256_file(resolved)
    except OSError as exc:
        raise ValueError(f"Не удалось прочитать исходный файл «{Path(path).name}».") from exc
    return SourceFingerprint(
        path=resolved,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        sha256=digest,
        device=st.st_dev if st.st_ino else None,
        inode=st.st_ino if st.st_ino else None,
    )


class OriginalGuard:
    """Thread-safe append-only registry protecting imported/opened originals and sources."""

    def __init__(self):
        self._lock = threading.RLock()
        self._paths: set[Path] = set()
        self._raw_paths: set[Path] = set()
        self._norm_paths: set[str] = set()
        self._file_ids: set[tuple[int, int]] = set()
        self._fingerprints: dict[Path, SourceFingerprint] = {}

    def register(self, path: str | Path, *, fingerprint: SourceFingerprint | None = None) -> Path:
        raw = Path(os.path.abspath(path))
        resolved = Path(path).resolve()
        with self._lock:
            self._raw_paths.add(raw)
            self._paths.add(resolved)
            self._norm_paths.add(os.path.normcase(str(raw)))
            self._norm_paths.add(os.path.normcase(str(resolved)))
            for candidate in (raw, resolved):
                try:
                    st = candidate.stat()
                except OSError:
                    continue
                if st.st_ino:
                    self._file_ids.add((st.st_dev, st.st_ino))
            if fingerprint is not None and resolved not in self._fingerprints:
                self._fingerprints[resolved] = fingerprint
        return resolved

    def register_many(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            self.register(path)

    def record_fingerprint(self, fingerprint: SourceFingerprint) -> None:
        resolved = self.register(fingerprint.path, fingerprint=fingerprint)
        with self._lock:
            self._fingerprints[resolved] = fingerprint

    def is_protected(self, target: str | Path) -> bool:
        raw = Path(os.path.abspath(target))
        resolved = Path(target).resolve()
        candidates = {raw, resolved}
        if raw.is_symlink():
            try:
                candidates.add(Path(os.readlink(raw)))
            except OSError:
                pass
        with self._lock:
            for candidate in candidates:
                if candidate in self._raw_paths or candidate in self._paths:
                    return True
                if os.path.normcase(str(candidate)) in self._norm_paths:
                    return True
            for candidate in (raw, resolved):
                try:
                    st = candidate.stat()
                except OSError:
                    continue
                if st.st_ino and (st.st_dev, st.st_ino) in self._file_ids:
                    return True
                for known in self._paths | self._raw_paths:
                    try:
                        if known.exists() and os.path.samefile(candidate, known):
                            return True
                    except OSError:
                        continue
            return False

    def assert_not_protected(self, target: str | Path) -> None:
        if self.is_protected(target):
            raise OriginalOverwriteError(
                "Нельзя перезаписать загруженный оригинал. Выберите другое имя файла."
            )

    def validate_source(
        self, path: str | Path, *, expected: SourceFingerprint | None = None
    ) -> None:
        resolved = Path(path).resolve()
        with self._lock:
            fp = expected if expected is not None else self._fingerprints.get(resolved)
        if fp is None:
            return
        if not fp.matches_current(resolved):
            raise SourceChangedError(
                f"Исходный файл «{Path(path).name}» изменился или был перемещён. "
                "Перезагрузите файл перед продолжением."
            )

    def snapshot_paths(self) -> frozenset[Path]:
        with self._lock:
            return frozenset(self._paths)


_DEFAULT_GUARD_LOCK = threading.Lock()
_DEFAULT_GUARD: OriginalGuard | None = None


def get_default_original_guard() -> OriginalGuard:
    """Return the process-wide append-only original-file guard."""
    global _DEFAULT_GUARD
    with _DEFAULT_GUARD_LOCK:
        if _DEFAULT_GUARD is None:
            _DEFAULT_GUARD = OriginalGuard()
        return _DEFAULT_GUARD


def reset_default_original_guard() -> None:
    """Reset the default guard (intended for test isolation)."""
    global _DEFAULT_GUARD
    with _DEFAULT_GUARD_LOCK:
        _DEFAULT_GUARD = OriginalGuard()


def _destination_lock(target: Path) -> threading.Lock:
    key = os.path.normcase(str(target.resolve()))
    with _DEST_LOCKS_GUARD:
        lock = _DEST_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _DEST_LOCKS[key] = lock
        return lock


def atomic_write_file(
    target: str | Path,
    writer_fn: Callable[[Path], None],
    *,
    overwrite: bool = False,
    validator_fn: Callable[[Path], None] | None = None,
    original_guard: OriginalGuard | None = None,
) -> Path:
    """Stage beside destination, validate, re-check guards, and publish safely.

    Note: Atomic replacement (`os.replace`) and atomic exclusive creation (`os.link`)
    are used where supported. On filesystems without hard links (e.g. FAT/exFAT or
    certain network shares), publishing a new file falls back to exclusive `xb`
    creation plus copying, which prevents overwriting existing files but is not
    atomic while copying. An unexpected process crash can still leave a `.cardprint-*.tmp`
    staging file behind.
    """
    target = Path(target)
    guard = original_guard if original_guard is not None else get_default_original_guard()
    with _destination_lock(target):
        guard.assert_not_protected(target)
        if target.exists() and not overwrite:
            raise FileExistsError(
                "Файл уже существует. Выберите другое имя или подтвердите замену."
            )
        fd, staging_name = tempfile.mkstemp(prefix=".cardprint-", suffix=".tmp", dir=target.parent)
        os.close(fd)
        staging = Path(staging_name)
        try:
            writer_fn(staging)
            if validator_fn is not None:
                validator_fn(staging)
            guard.assert_not_protected(target)
            if overwrite:
                os.replace(staging, target)
            else:
                try:
                    os.link(staging, target)
                except OSError as exc:
                    if exc.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV}:
                        raise
                    # FAT/exFAT and some network drives cannot create hard links.
                    # Exclusive creation prevents replacement, though copying is non-atomic.
                    with target.open("xb") as destination:
                        try:
                            with staging.open("rb") as source:
                                shutil.copyfileobj(source, destination)
                        except BaseException:
                            destination.close()
                            target.unlink(missing_ok=True)
                            raise
        finally:
            staging.unlink(missing_ok=True)
        return target


@dataclass(frozen=True)
class Sheet:
    images: dict[str, Image.Image]
    placements: list[Placement]


@dataclass(frozen=True)
class ExportSnapshot:
    images: dict[str, Image.Image]
    placements: tuple[Placement, ...]
    revisions: tuple[tuple[str, int], ...] = ()
    source_fingerprints: tuple[SourceFingerprint, ...] = ()


def snapshot_sheet(
    sheet: Sheet,
    *,
    revisions: Iterable[tuple[str, int]] = (),
    source_fingerprints: Iterable[SourceFingerprint] = (),
) -> ExportSnapshot:
    """Capture an immutable sheet snapshot so subsequent UI edits cannot mutate an export."""
    return ExportSnapshot(
        images={side: img.copy() for side, img in sheet.images.items()},
        placements=tuple(sheet.placements),
        revisions=tuple(revisions),
        source_fingerprints=tuple(source_fingerprints),
    )


def render_sheet(sheet: Sheet, dpi: float = 120) -> Image.Image:
    scale = dpi / 25.4
    result = Image.new("RGB", (round(A4_WIDTH_MM * scale), round(A4_HEIGHT_MM * scale)), "white")
    for item in sheet.placements:
        x, y = round(item.x_mm * scale), round(item.y_mm * scale)
        right, bottom = (
            round((item.x_mm + item.width_mm) * scale),
            round((item.y_mm + item.height_mm) * scale),
        )
        resized = sheet.images[item.side].resize((right - x, bottom - y), Image.Resampling.LANCZOS)
        result.paste(resized, (x, y))
    return result


def _write_pdf(sheet: Sheet, path: Path):
    pdf = canvas.Canvas(
        str(path), pagesize=(A4_WIDTH_MM * MM_TO_PT, A4_HEIGHT_MM * MM_TO_PT), pageCompression=1
    )
    pdf.setTitle("Копии документов — A4")
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(0, 0, A4_WIDTH_MM * MM_TO_PT, A4_HEIGHT_MM * MM_TO_PT, fill=1, stroke=0)
    for item in sheet.placements:
        pdf.drawImage(
            ImageReader(sheet.images[item.side]),
            item.x_mm * MM_TO_PT,
            (A4_HEIGHT_MM - item.y_mm - item.height_mm) * MM_TO_PT,
            item.width_mm * MM_TO_PT,
            item.height_mm * MM_TO_PT,
        )
    pdf.showPage()
    pdf.save()


def _validate_staged_pdf(staging: Path, pdf_worker: PdfWorkerClient | None) -> None:
    info = (pdf_worker or get_default_pdf_worker()).validate_pdf(staging, expected_pages=1)
    w_pt, h_pt = info.page_sizes_pt[0]
    if abs(w_pt - A4_WIDTH_MM * MM_TO_PT) > 0.5 or abs(h_pt - A4_HEIGHT_MM * MM_TO_PT) > 0.5:
        raise ValueError("Сохраняемый PDF не прошёл проверку формата A4.")


def _validate_staged_png(staging: Path) -> None:
    expected_size = (round(A4_WIDTH_MM * 300 / 25.4), round(A4_HEIGHT_MM * 300 / 25.4))
    try:
        with Image.open(staging) as img:
            img.load()
            size = img.size
            dpi = img.info.get("dpi")
    except Exception as exc:
        raise ValueError("Сохраняемый PNG не прошёл проверку изображения.") from exc
    if size != expected_size:
        raise ValueError("Сохраняемый PNG не прошёл проверку размера A4 300 DPI.")
    if not dpi or len(dpi) != 2 or any(abs(float(val) - 300) > 1.0 for val in dpi):
        raise ValueError("Сохраняемый PNG не прошёл проверку метаданных 300 DPI.")


def export_sheet(
    sheet: Sheet,
    target: str | Path,
    kind: str,
    *,
    overwrite: bool = False,
    pdf_worker: PdfWorkerClient | None = None,
    original_guard: OriginalGuard | None = None,
    source_fingerprints: Iterable[SourceFingerprint] = (),
):
    """Validate sources, stage beside destination, validate output, and publish safely."""
    if kind not in ("pdf", "png"):
        raise ValueError("Неизвестный формат экспорта.")
    guard = original_guard if original_guard is not None else get_default_original_guard()
    fingerprints = tuple(source_fingerprints)
    for fp in fingerprints:
        guard.validate_source(fp.path, expected=fp)

    def write(staging: Path) -> None:
        if kind == "pdf":
            _write_pdf(sheet, staging)
        else:
            render_sheet(sheet, dpi=300).save(staging, format="PNG", dpi=(300, 300))

    def validate(staging: Path) -> None:
        if kind == "pdf":
            _validate_staged_pdf(staging, pdf_worker)
        else:
            _validate_staged_png(staging)
        for fp in fingerprints:
            guard.validate_source(fp.path, expected=fp)

    return atomic_write_file(
        target,
        write,
        overwrite=overwrite,
        validator_fn=validate,
        original_guard=guard,
    )
