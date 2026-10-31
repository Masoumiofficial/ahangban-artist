from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ahangban_artists.errors import ValidationError
from ahangban_artists.profile import content_containment, disc_inside_circle, profile_geometry, safe_circle
from ahangban_artists.settings import EXPECTED_PALETTE, Settings
from ahangban_artists.utils import slugify_english


def test_fixed_brand_configuration_and_svg_layers(settings: Settings) -> None:
    assert settings.data["palette"] == EXPECTED_PALETTE
    assert settings.logo_path.name == "ahangban-logo-horizontal-reverse.svg"
    assert settings.logo_path.stat().st_size == 4456
    assert settings.width == settings.height == 1080

    blue = (settings.canonical_badges_path / "verified-blue.svg").read_text(encoding="utf-8")
    gold = (settings.canonical_badges_path / "verified-gold.svg").read_text(encoding="utf-8")
    ET.fromstring(blue)
    ET.fromstring(gold)
    assert "#185ADB" in blue
    assert "#D4AF37" not in blue
    assert "#D4AF37" in gold
    assert "#185ADB" not in gold


def test_pinned_fonts_and_profile_layout(settings: Settings) -> None:
    assert settings.design_version == "1.1.0"
    assert settings.data["fonts"]["family"] == "Vazirmatn"
    assert set(settings.font_files) == {400, 700}
    for path in settings.font_files.values():
        assert path.is_file()
        assert path.suffix == ".ttf"
        assert path.stat().st_size > 40_000
    assert settings.font_license_path.read_text(encoding="utf-8").startswith("Copyright")
    assert "SIL OPEN FONT LICENSE" in settings.font_license_path.read_text(encoding="utf-8")

    profile = settings.profile_layout
    assert profile["enabled"] is True
    assert profile["portrait_fit"] == "medallion"
    assert profile["name_fa"]["weight"] == 700
    assert profile["name_en"]["uppercase"] is True
    assert profile["verification_badge"]["anchor"] == "disc_edge"
    # حلقهٔ مدالیون باید داخل دایرهٔ امن بماند و عناصر متنی داخل حلقه
    medallion = profile_geometry(settings)["medallion"]
    center, radius = safe_circle(settings)
    assert disc_inside_circle((medallion["center_x"], medallion["center_y"]), medallion["outer_radius"], center, radius)
    assert content_containment(settings)[1] < radius


def test_missing_font_file_blocks_the_pipeline(system_root: Path) -> None:
    (system_root / "brand-kit" / "fonts" / "Vazirmatn-Bold.ttf").unlink()
    with pytest.raises(ValidationError, match="فایل فونت پین‌شده"):
        Settings.load(system_root)


def test_english_slug_does_not_modify_display_name() -> None:
    assert slugify_english("MohammadReza  Shajarian") == "mohammadreza-shajarian"
    assert slugify_english("D'Angelo") == "d-angelo"
