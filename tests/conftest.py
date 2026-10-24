from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from ahangban_artists.settings import Settings


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def system_root(tmp_path: Path) -> Path:
    (tmp_path / "brand-kit" / "prompts").mkdir(parents=True)
    (tmp_path / "brand-kit" / "artists").mkdir()
    (tmp_path / "logo-ahangban").mkdir()
    shutil.copy2(REPOSITORY_ROOT / "brand-kit" / "cover-system.json", tmp_path / "brand-kit" / "cover-system.json")
    shutil.copy2(
        REPOSITORY_ROOT / "brand-kit" / "prompts" / "artist-portrait-fa.txt",
        tmp_path / "brand-kit" / "prompts" / "artist-portrait-fa.txt",
    )
    shutil.copytree(REPOSITORY_ROOT / "brand-kit" / "badges", tmp_path / "brand-kit" / "badges")
    shutil.copy2(
        REPOSITORY_ROOT / "logo-ahangban" / "ahangban-logo-horizontal-reverse.svg",
        tmp_path / "logo-ahangban" / "ahangban-logo-horizontal-reverse.svg",
    )
    return tmp_path


@pytest.fixture
def settings(system_root: Path) -> Settings:
    return Settings.load(system_root)


@pytest.fixture
def image_factory():
    def create(path: Path, size: tuple[int, int] = (1200, 1200), mode: str = "RGB") -> Path:
        if mode == "L":
            image = Image.new(mode, size, 32)
            draw = ImageDraw.Draw(image)
            draw.ellipse((size[0] * 0.3, size[1] * 0.15, size[0] * 0.7, size[1] * 0.65), fill=170)
            draw.rectangle((size[0] * 0.2, size[1] * 0.65, size[0] * 0.8, size[1]), fill=75)
        else:
            image = Image.new(mode, size, (34, 34, 34))
            draw = ImageDraw.Draw(image)
            draw.ellipse((size[0] * 0.3, size[1] * 0.15, size[0] * 0.7, size[1] * 0.65), fill=(166, 140, 122))
            draw.rectangle((size[0] * 0.2, size[1] * 0.65, size[0] * 0.8, size[1]), fill=(48, 50, 54))
        image.save(path)
        return path

    return create
