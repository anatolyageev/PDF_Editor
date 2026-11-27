"""Pure-Python domain models and memory-bounded parametric Undo/Redo (no Qt dependency)."""

from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .geometry import CardSize, Layout
from .imaging import Source

SIDE_NAMES = {"front": "Лицевая сторона", "back": "Обратная сторона"}

DEFAULT_HISTORY_MAX_ENTRIES = 50
DEFAULT_HISTORY_MAX_BYTES = 256 * 1024 * 1024


def _normalize_points(points: tuple) -> tuple[tuple[float, float], ...]:
    return tuple((float(x), float(y)) for x, y in points)


@dataclass(frozen=True)
class CardSnapshot:
    """Immutable parameter snapshot sharing the read-only original pixel buffer."""

    source: Source
    original: np.ndarray
    points: tuple[tuple[float, float], ...]
    turns: int
    radius: float
    size: CardSize

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CardSnapshot):
            return NotImplemented
        return (
            self.source == other.source
            and self.original is other.original
            and self.points == other.points
            and self.turns == other.turns
            and self.radius == other.radius
            and self.size == other.size
        )


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Immutable workspace parameter snapshot for Undo/Redo."""

    cards: tuple[tuple[str, CardSnapshot], ...] = ()
    layout: Layout = field(default_factory=Layout)

    @classmethod
    def from_state(cls, cards: dict[str, "Card"], layout: Layout) -> "WorkspaceSnapshot":
        ordered = tuple(
            (side, cards[side].snapshot()) for side in ("front", "back") if side in cards
        )
        return cls(cards=ordered, layout=layout)

    def card_map(self) -> dict[str, CardSnapshot]:
        return dict(self.cards)


@dataclass
class Card:
    source: Source
    original: np.ndarray
    points: tuple
    turns: int = 0
    radius: float = 0
    size: CardSize = field(default_factory=CardSize)
    revision: int = 0
    ready_revision: int = -1
    cropped: Image.Image | None = None
    rendered: Image.Image | None = None
    display: Image.Image | None = None
    display_turns: int = 0
    error: str | None = None

    @property
    def source_size(self) -> tuple[int, int]:
        h, w = self.original.shape[:2]
        return (h, w) if self.turns % 2 else (w, h)

    def snapshot(self) -> CardSnapshot:
        return CardSnapshot(
            source=self.source,
            original=self.original,
            points=_normalize_points(self.points),
            turns=self.turns % 4,
            radius=float(self.radius),
            size=self.size,
        )

    @classmethod
    def from_snapshot(cls, snapshot: CardSnapshot, *, revision: int = 0) -> "Card":
        return cls(
            source=snapshot.source,
            original=snapshot.original,
            points=snapshot.points,
            turns=snapshot.turns % 4,
            radius=snapshot.radius,
            size=snapshot.size,
            revision=revision,
            ready_revision=-1,
            cropped=None,
            rendered=None,
            display=None,
            display_turns=-1,
            error=None,
        )


@dataclass(frozen=True)
class HistoryEntry:
    snapshot: WorkspaceSnapshot
    label: str = ""


@dataclass(frozen=True)
class PushResult:
    recorded: bool
    evicted: int = 0
    evicted_for_memory: int = 0


class UndoStack:
    """Bounded parametric Undo/Redo stack capping both steps and history-only buffer bytes."""

    def __init__(
        self,
        initial: WorkspaceSnapshot | None = None,
        *,
        max_entries: int = DEFAULT_HISTORY_MAX_ENTRIES,
        max_bytes: int = DEFAULT_HISTORY_MAX_BYTES,
    ):
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1.")
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative.")
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._entries: list[HistoryEntry] = [
            HistoryEntry(initial if initial is not None else WorkspaceSnapshot())
        ]
        self._cursor = 0
        self.evicted_count = 0
        self.evicted_for_memory_count = 0

    @property
    def current(self) -> WorkspaceSnapshot:
        return self._entries[self._cursor].snapshot

    @property
    def can_undo(self) -> bool:
        return self._cursor > 0

    @property
    def can_redo(self) -> bool:
        return self._cursor < len(self._entries) - 1

    @property
    def undo_depth(self) -> int:
        return self._cursor

    @property
    def redo_depth(self) -> int:
        return len(self._entries) - 1 - self._cursor

    @property
    def undo_label(self) -> str | None:
        return self._entries[self._cursor].label if self.can_undo else None

    @property
    def redo_label(self) -> str | None:
        return self._entries[self._cursor + 1].label if self.can_redo else None

    @property
    def history_only_bytes(self) -> int:
        """Bytes of original arrays retained solely by non-current history entries (counted once)."""
        active_ids = {id(snap.original) for _, snap in self.current.cards}
        seen: dict[int, int] = {}
        for idx, entry in enumerate(self._entries):
            if idx == self._cursor:
                continue
            for _, snap in entry.snapshot.cards:
                buf_id = id(snap.original)
                if buf_id not in active_ids and buf_id not in seen:
                    seen[buf_id] = int(snap.original.nbytes)
        return sum(seen.values())

    def push(self, snapshot: WorkspaceSnapshot, label: str = "") -> PushResult:
        if snapshot == self.current:
            return PushResult(recorded=False)
        del self._entries[self._cursor + 1 :]
        self._entries.append(HistoryEntry(snapshot, label))
        self._cursor = len(self._entries) - 1

        evicted = 0
        evicted_for_memory = 0
        while self._cursor > self.max_entries:
            self._entries.pop(0)
            self._cursor -= 1
            evicted += 1
        while self._cursor > 0 and self.history_only_bytes > self.max_bytes:
            self._entries.pop(0)
            self._cursor -= 1
            evicted += 1
            evicted_for_memory += 1

        self.evicted_count += evicted
        self.evicted_for_memory_count += evicted_for_memory
        return PushResult(recorded=True, evicted=evicted, evicted_for_memory=evicted_for_memory)

    def undo(self) -> tuple[WorkspaceSnapshot, str] | None:
        if not self.can_undo:
            return None
        label = self._entries[self._cursor].label
        self._cursor -= 1
        return self.current, label

    def redo(self) -> tuple[WorkspaceSnapshot, str] | None:
        if not self.can_redo:
            return None
        self._cursor += 1
        return self.current, self._entries[self._cursor].label
