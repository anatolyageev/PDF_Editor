"""Pure-Python domain models for CardPrint (no Qt dependency)."""

from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .geometry import CardSize
from .imaging import Source

SIDE_NAMES = {"front": "Лицевая сторона", "back": "Обратная сторона"}


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
