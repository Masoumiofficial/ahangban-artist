from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageCms

from ahangban_artists.errors import ExistingArtistError, ValidationError
from ahangban_artists.images import inspect_png
from ahangban_artists.project import (
    ArtistProject,
    approve_visual_review,
    finalize,
    intake,
    set_verification,
)
from ahangban_artists.settings import Settings
from ahangban_artists.utils import sha256_file


def test_intake_preserves_inputs_and_defaults_unverified(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference.png")
    original_bytes = reference.read_bytes()
    project = intake(settings, reference, "هنرمند\u200c آزمایشی", "Synthetic Artist")

    assert project.metadata["name_fa"] == "هنرمند\u200c آزمایشی"
    assert project.metadata["name_en"] == "Synthetic Artist"
    assert project.slug == "synthetic-artist"
    assert project.metadata["verification"] == {
        "status": "unverified",
        "verified": False,
        "source": "default-unverified",
        "updated_at": project.metadata["verification"]["updated_at"],
        "note": "Verification is project data; a badge on an image is not identity evidence.",
    }
    assert project.reference_path.read_bytes() == original_bytes
    assert project.metadata["generation"]["status"] == "awaiting-external-master"
    assert not project.master_path.exists()
    assert not project.final_path.exists()
    assert all((project.artist_dir / "badges" / name).is_file() for name in settings.badge_layer_names)
    assert json.loads(project.metadata_path.read_text(encoding="utf-8"))["slug"] == "synthetic-artist"


def test_verified_acceptance_workflow_has_clean_master_and_matching_outputs(
    settings: Settings, image_factory, tmp_path: Path
) -> None:
    reference = image_factory(tmp_path / "reference.png")
    external_master = image_factory(tmp_path / "external-master.png")
    project = intake(
        settings,
        reference,
        "آزمون سیستم",
        "Synthetic Acceptance",
        verified=True,
        verification_explicit=True,
    )
    project, report = finalize(settings, project.slug, external_master, generator="synthetic-test-fixture")

    assert report["technical_status"] == "pass"
    assert report["acceptance_status"] == "pending-visual-review"
    assert project.master_path.is_file()
    assert project.final_path.is_file()
    assert project.preview_path(True).is_file()
    assert not project.preview_path(False).exists()
    for path, mode in (
        (project.master_path, "RGB"),
        (project.final_path, "RGB"),
        (project.preview_path(True), "RGBA"),
    ):
        info = inspect_png(path)
        assert (info["width"], info["height"]) == (1080, 1080)
        assert info["format"] == "PNG"
        assert info["mode"] == mode
        assert info["has_icc_profile"]
        assert info["srgb_profile"]
    assert project.master_path.read_bytes() != project.final_path.read_bytes()
    assert project.metadata["revision"] == 1
    assert project.changelog_path.is_file()


def test_unverified_output_has_no_verification_preview(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "master.png")
    project = intake(settings, reference, "آزمون بدون نشان", "No Badge Test")
    project, report = finalize(settings, project.slug, master)

    assert project.metadata["verification"]["status"] == "unverified"
    assert project.preview_path(False).is_file()
    assert not project.preview_path(True).exists()
    assert report["technical_status"] == "pass"


def test_replacing_final_requires_explicit_decision_and_archives_previous(
    settings: Settings, image_factory, tmp_path: Path
) -> None:
    reference = image_factory(tmp_path / "reference.png")
    first_master = image_factory(tmp_path / "first.png")
    second_master = image_factory(tmp_path / "second.png", size=(1000, 1400))
    project = intake(settings, reference, "آزمون نسخه", "Revision Test")
    project, _ = finalize(settings, project.slug, first_master)
    original_final_hash = sha256_file(project.final_path)
    original_reference_hash = sha256_file(project.reference_path)

    with pytest.raises(ExistingArtistError):
        finalize(settings, project.slug, second_master, on_existing="keep")
    assert sha256_file(project.final_path) == original_final_hash

    with pytest.raises(ValidationError):
        finalize(settings, project.slug, second_master, on_existing="archive", change="")
    assert sha256_file(project.final_path) == original_final_hash

    project, report = finalize(
        settings,
        project.slug,
        second_master,
        on_existing="archive",
        change="آزمون بایگانی و جایگزینی نسخه",
    )
    archive = project.artist_dir / "history" / "revisions" / "v001"
    assert report["technical_status"] == "pass"
    assert project.metadata["revision"] == 2
    assert (archive / "portrait" / "artist-master.png").is_file()
    assert (archive / "portrait" / "artist-1080x1080.png").is_file()
    assert (archive / "metadata" / "artist.json").is_file()
    assert sha256_file(project.reference_path) == original_reference_hash
    assert sha256_file(project.final_path) != original_final_hash


def test_verification_change_does_not_modify_master(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "master.png")
    project = intake(settings, reference, "آزمون وضعیت", "Status Test")
    project, _ = finalize(settings, project.slug, master)
    master_hash = sha256_file(project.master_path)
    unverified_final_hash = sha256_file(project.final_path)

    project, report, changed = set_verification(settings, project.slug, True, "تأیید صریح آزمایشی")
    assert changed
    assert report["technical_status"] == "pass"
    assert project.metadata["verification"]["source"] == "user-explicit"
    assert project.metadata["revision"] == 2
    assert sha256_file(project.master_path) == master_hash
    assert sha256_file(project.final_path) != unverified_final_hash
    assert project.preview_path(True).is_file()
    assert not project.preview_path(False).exists()
    archive = project.artist_dir / "history" / "revisions" / "v001"
    assert (archive / "portrait" / "artist-unverified-preview.png").is_file()
    assert not (archive / "portrait" / "artist-master.png").exists()

    project, report, changed = set_verification(settings, project.slug, False, "حذف صریح آزمایشی نشان")
    assert changed
    assert report["technical_status"] == "pass"
    assert project.metadata["revision"] == 3
    assert sha256_file(project.master_path) == master_hash
    assert project.preview_path(False).is_file()
    assert not project.preview_path(True).exists()


def test_qc_detects_final_tampering(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "master.png")
    project = intake(settings, reference, "آزمون کنترل", "Tamper Test")
    project, _ = finalize(settings, project.slug, master)

    with Image.open(project.final_path) as source:
        tampered = source.convert("RGB")
    pixel = tampered.getpixel((0, 0))
    tampered.putpixel((0, 0), tuple(255 - channel for channel in pixel))
    tampered.save(project.final_path)

    report = project.refresh_qc()
    assert report["technical_status"] == "fail"
    failed_ids = {item["id"] for item in report["checks"] if item["status"] == "fail"}
    assert "final_matches_master_logo_and_verification" in failed_ids


def test_qc_rejects_non_srgb_profile_even_with_unchanged_pixels(
    settings: Settings, image_factory, tmp_path: Path
) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "master.png")
    project = intake(settings, reference, "آزمون پروفایل", "Profile Test")
    project, _ = finalize(settings, project.slug, master)

    with Image.open(project.final_path) as source:
        pixels = source.convert("RGB")
    lab_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
    pixels.save(project.final_path, format="PNG", icc_profile=lab_profile)

    report = project.refresh_qc()
    assert report["technical_status"] == "fail"
    assert report["files"]["final"]["srgb_profile"] is False
    failed_ids = {item["id"] for item in report["checks"] if item["status"] == "fail"}
    assert "final_png_1080_srgb" in failed_ids


def test_non_square_master_uses_proportional_contain(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "wide.png", size=(1600, 800))
    project = intake(settings, reference, "آزمون قاب", "Contain Test")
    project, report = finalize(settings, project.slug, master)

    transform = project.metadata["generation"]["transform"]
    assert transform["resized_width"] == 1080
    assert transform["resized_height"] == 540
    assert transform["offset_y"] == 270
    assert report["technical_status"] == "pass"


def test_upscale_remains_visible_as_a_limitation_after_approval(
    settings: Settings, image_factory, tmp_path: Path
) -> None:
    reference = image_factory(tmp_path / "reference.png")
    small_master = image_factory(tmp_path / "small-master.png", size=(500, 500))
    project = intake(settings, reference, "آزمون وضوح", "Upscale Report Test")
    project, _ = finalize(settings, project.slug, small_master)
    project, report = approve_visual_review(settings, project.slug, "Synthetic review state test")

    assert report["acceptance_status"] == "pass"
    assert project.metadata["generation"]["transform"]["upscaled"]
    assert any("upscale" in limitation for limitation in report["limitations"])


def test_grayscale_input_is_not_colorized(settings: Settings, image_factory, tmp_path: Path) -> None:
    reference = image_factory(tmp_path / "reference-gray.png", mode="L")
    master = image_factory(tmp_path / "master-gray.png", mode="L")
    project = intake(settings, reference, "آزمون خاکستری", "Grayscale Test")
    assert project.metadata["reference"]["appears_grayscale"]
    project, _ = finalize(settings, project.slug, master)

    with Image.open(project.master_path) as image:
        red, green, blue = image.convert("RGB").split()
        assert red.tobytes() == green.tobytes() == blue.tobytes()


def test_visual_approval_is_bound_to_revision_and_file_hashes(
    settings: Settings, image_factory, tmp_path: Path
) -> None:
    reference = image_factory(tmp_path / "reference.png")
    master = image_factory(tmp_path / "master.png")
    replacement = image_factory(tmp_path / "replacement.png", size=(1000, 1300))
    project = intake(settings, reference, "آزمون تأیید بصری", "Review State Test")
    project, _ = finalize(settings, project.slug, master)
    project, report = approve_visual_review(
        settings,
        project.slug,
        "Synthetic state-machine test",
        "فقط آزمون وضعیت نرم‌افزار، نه تأیید هویت یک شخص واقعی",
    )
    assert report["acceptance_status"] == "pass"
    assert project.metadata["generation"]["status"] == "accepted"
    assert project.metadata["updated_at"] == project.metadata["review"]["reviewed_at"]
    assert not project.changelog_path.read_text(encoding="utf-8").endswith("\n\n")

    project, report = finalize(
        settings,
        project.slug,
        replacement,
        on_existing="archive",
        change="آزمون بی‌اعتبارشدن تأیید قبلی",
    )
    assert project.metadata["review"]["status"] == "pending"
    assert report["acceptance_status"] == "pending-visual-review"


def test_possible_duplicate_names_need_explicit_override(
    settings: Settings, image_factory, tmp_path: Path
) -> None:
    first_reference = image_factory(tmp_path / "first.png")
    second_reference = image_factory(tmp_path / "second.png")
    intake(settings, first_reference, "نام مشترک", "First Artist")
    with pytest.raises(ExistingArtistError):
        intake(settings, second_reference, "نام مشترک", "Second Artist")

    project = intake(
        settings,
        second_reference,
        "نام مشترک",
        "Second Artist",
        allow_duplicate_names=True,
    )
    assert project.metadata["intake_duplicate_name_override"]["matching_slugs"] == ["first-artist"]
