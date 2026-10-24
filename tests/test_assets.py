from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

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


def test_english_slug_does_not_modify_display_name() -> None:
    assert slugify_english("MohammadReza  Shajarian") == "mohammadreza-shajarian"
    assert slugify_english("D'Angelo") == "d-angelo"
