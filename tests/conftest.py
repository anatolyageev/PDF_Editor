import os

# Headless by default; explicitly set QT_QPA_PLATFORM=xcb for a real display run.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def synthetic_card():
    image = Image.new("RGB", (856, 540), (207, 226, 237))
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, 852, 536), outline=(30, 70, 110), width=4)
    draw.rectangle((45, 90, 210, 360), fill=(180, 90, 70))
    for y in range(100, 430, 35):
        draw.line((260, y, 760, y), fill=(30, 40, 55), width=3)
    draw.text((260, 45), "SYNTHETIC TEST CARD - NO PERSONAL DATA", fill=(10, 20, 30))
    return image


@pytest.fixture
def original(synthetic_card):
    array = np.array(synthetic_card)
    array.flags.writeable = False
    return array
