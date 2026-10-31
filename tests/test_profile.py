from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from ahangban_artists.errors import ValidationError
from ahangban_artists.images import compose_output, inspect_png, load_png, png_bytes
from ahangban_artists.profile import (
    content_containment,
    disc_inside_circle,
    fit_line,
    profile_geometry,
    rect_inside_circle,
    render_name_layer,
    safe_circle,
)
from ahangban_artists.project import finalize, intake
from ahangban_artists.settings import Settings
from ahangban_artists.utils import read_json, sha256_bytes, sha256_file


def _bright_ink(crop: Image.Image) -> int:
    """تعداد پیکسل‌های روشن؛ برای اثبات حضور جوهر متن/لوگو روی خروجی."""
    gray = crop.convert("L").point(lambda value: 255 if value > 200 else 0)
    return gray.histogram()[255]


def test_name_layer_is_rtl_shaped_and_deterministic(settings: Settings) -> None:
    first, first_info = render_name_layer(settings, "محمدرضا شجریان", "Mohammadreza Shajarian")
    second, second_info = render_name_layer(settings, "محمدرضا شجریان", "Mohammadreza Shajarian")

    assert sha256_bytes(png_bytes(first)) == sha256_bytes(png_bytes(second))
    assert first_info == second_info

    fa_line = first_info["lines"][0]
    en_line = first_info["lines"][1]
    assert fa_line["text"] == "محمدرضا شجریان"
    assert fa_line["weight"] == 700
    assert en_line["weight"] == 400
    assert en_line["text"] == "MOHAMMADREZA SHAJARIAN"

    # یک خط راست‌به‌چپِ صحیح باید کشیده باشد، نه ستونیِ عمودی از حروف جدا
    fa_left, fa_top, fa_right, fa_bottom = fa_line["ink_bbox"]
    assert fa_right - fa_left > (fa_bottom - fa_top) * 2

    center, radius = content_containment(settings, profile_geometry(settings))
    for line in first_info["lines"]:
        assert rect_inside_circle(tuple(line["ink_bbox"]), center, radius)
    assert rect_inside_circle(tuple(first_info["element_bboxes"]["divider"]), center, radius)


def test_names_never_cross_the_medallion_ring(settings: Settings) -> None:
    geometry = profile_geometry(settings)
    center, radius = safe_circle(settings)
    medallion = geometry["medallion"]
    assert disc_inside_circle((medallion["center_x"], medallion["center_y"]), medallion["outer_radius"], center, radius)
    assert radius > geometry["medallion"]["outer_radius"] >= radius - 24

    for name_fa, name_en in (
        ("شهرام نظری", "Shahram Nazari"),
        ("گروه آوازی تهران با همخوانی", "Tehran Vocal Ensemble With Choir"),
        ("علی", "Ali"),
    ):
        _, info = render_name_layer(settings, name_fa, name_en)
        containment_center, containment_radius = content_containment(settings, geometry)
        for element, box in info["element_bboxes"].items():
            assert rect_inside_circle(tuple(box), containment_center, containment_radius), element


def test_overlong_name_is_refused_instead_of_spilling_outside(settings: Settings) -> None:
    very_long = "سلطان حسین استانبولی بیاتک ترکی همدانی اصفهانی کاشانی"
    with pytest.raises(ValidationError, match="جا نمی‌شود"):
        fit_line(settings, very_long, "name_fa")


def test_font_selection_cannot_fall_back_to_system_fonts(settings: Settings) -> None:
    settings.data["fonts"]["family"] = "FontThatDoesNotExistAnywhere"
    with pytest.raises(ValidationError, match="هیچ گلیف"):
        fit_line(settings, "محمدرضا شجریان", "name_fa")


def test_finalize_writes_names_logo_and_geometry_into_output_and_metadata(
    settings: Settings, image_factory, tmp_path: Path
) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "master.png")
    project = intake(settings, reference, "محمدرضا شجریان", "Mohammadreza Shajarian")
    project, report = finalize(settings, project.slug, master, generator="synthetic-fixture")

    assert report["technical_status"] == "pass"
    final_info = inspect_png(project.final_path)
    assert (final_info["width"], final_info["height"]) == (1080, 1080)

    with Image.open(project.master_path) as master_image, Image.open(project.final_path) as final_image:
        master_rgb = master_image.convert("RGB")
        final_rgb = final_image.convert("RGB")
        assert final_rgb.size == master_rgb.size
        # master باید بدون لایهٔ برند بماند؛ خروجی باید جوهر روشن نام و لوگو را داشته باشد
        name_ink = _bright_ink(final_rgb.crop((150, 640, 930, 770)))
        logo_ink = _bright_ink(final_rgb.crop((300, 815, 780, 890)))
        assert name_ink > 5000
        assert logo_ink > 300
        assert _bright_ink(master_rgb.crop((150, 640, 930, 770))) == 0

    profile = project.metadata["profile"]
    assert profile["badge"]["applied"] is False
    assert [line["role"] for line in profile["names"]["lines"]] == ["name_fa", "name_en"]
    assert profile["names"]["lines"][0]["text"] == "محمدرضا شجریان"
    assert profile["logo"]["visible_width"] == settings.profile_layout["logo"]["visible_width"]
    assert profile["safe_circle"]["radius"] == pytest.approx(432.0)

    recorded = read_json(project.metadata_path)["profile"]
    assert recorded["names"]["lines"] == profile["names"]["lines"]


def test_output_is_reproducible_from_master_and_metadata(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "master.png")
    project = intake(settings, reference, "کامرون موفاق", "Kamron Movaghar")
    project, _ = finalize(settings, project.slug, master, generator="synthetic-fixture")

    again, _ = compose_output(
        load_png(project.master_path),
        settings,
        project.artist_dir,
        project.verified,
        name_fa=project.metadata["name_fa"],
        name_en=project.metadata["name_en"],
        content_box=(0, 0, settings.width, settings.height),
    )
    assert png_bytes(again.convert("RGB")) == project.final_path.read_bytes()


def test_qc_detects_font_tampering(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "master.png")
    project = intake(settings, reference, "آزمون فونت", "Font Tamper Test")
    project, _ = finalize(settings, project.slug, master, generator="synthetic-fixture")
    assert project.refresh_qc()["technical_status"] == "pass"

    font_path = settings.font_files[700]
    font_path.write_bytes(font_path.read_bytes() + b"\x00")

    report = project.refresh_qc()
    assert report["technical_status"] == "fail"
    failed = {item["id"] for item in report["checks"] if item["status"] == "fail"}
    assert "fixed_brand_assets_unmodified" in failed


def test_intake_records_pinned_font_hashes(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference.png")
    project = intake(settings, reference, "آزمون ثبت فونت", "Font Record Test")
    recorded = project.metadata["assets"]["font_sha256"]

    assert set(recorded) == {path.name for path in settings.font_files.values()}
    for name, digest in recorded.items():
        path = settings.root / "brand-kit" / "fonts" / name
        assert sha256_file(path) == digest
