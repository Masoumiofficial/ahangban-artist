from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import ValidationError
from .images import (
    _official_logo,
    circular_preview,
    compose_final,
    images_equal,
    inspect_png,
    load_png,
)
from .settings import Settings
from .utils import relative_path, sha256_file, utc_now


def _rectangle_inside_circle(position: tuple[int, int], size: tuple[int, int], canvas: int) -> bool:
    x, y = position
    width, height = size
    center = canvas / 2
    radius_squared = center * center
    corners = ((x, y), (x + width, y), (x, y + height), (x + width, y + height))
    return all((corner_x - center) ** 2 + (corner_y - center) ** 2 <= radius_squared for corner_x, corner_y in corners)


def run_qc(settings: Settings, artist_dir: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    files: dict[str, Any] = {}

    def check(check_id: str, passed: bool, detail: str) -> None:
        checks.append({"id": check_id, "status": "pass" if passed else "fail", "detail": detail})

    def inspect(label: str, path: Path, expected_mode: str) -> bool:
        try:
            info = inspect_png(path)
            info["path"] = relative_path(path, settings.root)
            files[label] = info
            valid = (
                info["width"] == settings.width
                and info["height"] == settings.height
                and info["mode"] == expected_mode
                and info["srgb_profile"]
            )
            check(
                f"{label}_png_1080_srgb",
                valid,
                f"PNG سالم؛ انتظار: 1080×1080، {expected_mode}، دارای پروفایل sRGB. نتیجه: "
                f"{info['width']}×{info['height']}، {info['mode']}، sRGB={info['srgb_profile']} "
                f"({info['icc_profile_description'] or 'بدون پروفایل معتبر'})",
            )
            return valid
        except ValidationError as exc:
            check(f"{label}_png_1080_srgb", False, str(exc))
            return False

    reference_path: Path | None = None
    try:
        reference_path = settings.path(metadata["paths"]["reference"])
        expected_hash = metadata["reference"]["sha256"]
        actual_hash = sha256_file(reference_path) if reference_path.is_file() else "missing"
        in_source_directory = reference_path.parent == artist_dir / "source"
        check(
            "reference_unmodified",
            actual_hash == expected_hash and in_source_directory,
            f"هش مرجع و مسیر source بررسی شد؛ expected={expected_hash}, actual={actual_hash}",
        )
        files["reference"] = {
            "path": relative_path(reference_path, settings.root),
            "sha256": actual_hash,
            "width": metadata["reference"].get("width"),
            "height": metadata["reference"].get("height"),
            "format": metadata["reference"].get("format"),
        }
    except (KeyError, ValidationError, OSError) as exc:
        check("reference_unmodified", False, f"بررسی مرجع ممکن نبود: {exc}")

    verification = metadata.get("verification", {})
    verified = verification.get("verified")
    verification_consistent = (
        isinstance(verified, bool)
        and verification.get("status") == ("verified" if verified else "unverified")
        and verification.get("source") in {"user-explicit", "default-unverified"}
    )
    check(
        "verification_data_consistent",
        verification_consistent,
        "وضعیت، مقدار بولین و منبع صریح/پیش‌فرض باید سازگار باشند؛ استنباط از چهره مجاز نیست.",
    )

    assets = metadata.get("assets", {})
    try:
        actual_logo_hash = sha256_file(settings.logo_path)
        logo_ok = actual_logo_hash == assets.get("logo_sha256")
    except OSError:
        logo_ok = False
    badge_results = []
    for layer_name in settings.badge_layer_names:
        layer_path = artist_dir / "badges" / layer_name
        badge_results.append(
            layer_path.is_file() and sha256_file(layer_path) == assets.get("badge_sha256", {}).get(layer_name)
        )
    check(
        "fixed_brand_assets_unmodified",
        logo_ok and all(badge_results),
        "هش فایل رسمی لوگو و لایه‌های SVG مستقل با فرادادهٔ پروژه مقایسه شد.",
    )

    design_version_ok = metadata.get("design_version") == settings.design_version
    check(
        "design_version_consistent",
        design_version_ok,
        f"طراحی پروژه={metadata.get('design_version')}، طراحی ابزار={settings.design_version}",
    )

    master_path = artist_dir / "portrait" / "artist-master.png"
    final_path = artist_dir / "portrait" / "artist-1080x1080.png"
    active_preview_path = artist_dir / "portrait" / (
        "artist-verified-preview.png" if verified else "artist-unverified-preview.png"
    )
    inactive_preview_path = artist_dir / "portrait" / (
        "artist-unverified-preview.png" if verified else "artist-verified-preview.png"
    )

    master_ok = inspect("master", master_path, "RGB")
    final_ok = inspect("final", final_path, "RGB")
    preview_ok = inspect("preview", active_preview_path, "RGBA")
    check(
        "only_active_verification_preview",
        not inactive_preview_path.exists(),
        f"پیش‌نمایش وضعیت مخالف نباید وجود داشته باشد: {inactive_preview_path.name}",
    )

    expected_paths_ok = (
        metadata.get("paths", {}).get("master") == relative_path(master_path, settings.root)
        and metadata.get("paths", {}).get("final") == relative_path(final_path, settings.root)
        and metadata.get("paths", {}).get("preview") == relative_path(active_preview_path, settings.root)
    )
    check(
        "metadata_output_paths",
        expected_paths_ok,
        "مسیرهای master/final/preview در فراداده باید با وضعیت و ساختار واقعی مطابقت داشته باشند.",
    )

    if master_ok and final_ok and preview_ok and verification_consistent:
        try:
            master_image = load_png(master_path)
            actual_final = load_png(final_path)
            actual_preview = load_png(active_preview_path)
            expected_final = compose_final(master_image, settings, artist_dir, verified)
            expected_preview = circular_preview(expected_final)
            final_matches = images_equal(actual_final, expected_final)
            preview_matches = images_equal(actual_preview, expected_preview)
            check(
                "final_matches_master_logo_and_verification",
                final_matches,
                "خروجی از master بدون نشان، لوگوی رسمی و نشانِ مطابق metadata به‌صورت قطعی بازتولید و مقایسه شد.",
            )
            check(
                "circular_preview_matches_final",
                preview_matches,
                "پیش‌نمایش شفاف دایره‌ای دقیقاً از خروجی وضعیت فعلی ساخته شده است.",
            )
        except ValidationError as exc:
            check("deterministic_output_comparison", False, str(exc))
    else:
        check(
            "deterministic_output_comparison",
            False,
            "به‌دلیل فایل/فرادادهٔ نامعتبر، بازتولید و مقایسهٔ قطعی خروجی ممکن نبود.",
        )

    try:
        logo, logo_position = _official_logo(settings)
        badge_layout = settings.badge_layout
        badge_size = (int(badge_layout["size"]), int(badge_layout["size"]))
        badge_position = (int(badge_layout["x"]), int(badge_layout["y"]))
        logo_inside = _rectangle_inside_circle(logo_position, logo.size, settings.width)
        badge_inside = _rectangle_inside_circle(badge_position, badge_size, settings.width)
        check(
            "brand_layers_inside_circular_crop",
            logo_inside and badge_inside,
            "قاب‌بندی محافظه‌کارانهٔ لوگو و نشان داخل دایرهٔ 1080 بررسی شد؛ امنی چهره نیازمند بازبینی بصری است.",
        )
    except (KeyError, ValueError, ValidationError) as exc:
        check("brand_layers_inside_circular_crop", False, str(exc))

    technical_status = "pass" if all(item["status"] == "pass" for item in checks) else "fail"
    review = metadata.get("review", {})
    review_checks = review.get("checks", {})
    required_approvals = {
        "identity_preserved",
        "face_safe_in_central_80_percent_and_circle",
        "readable_at_small_size",
        "no_unintended_text_or_watermark",
        "consistent_editorial_brand_direction",
    }
    review_matches_revision = (
        review.get("status") == "approved"
        and review.get("revision") == metadata.get("revision")
        and review.get("master_sha256") == files.get("master", {}).get("sha256")
        and review.get("final_sha256") == files.get("final", {}).get("sha256")
        and all(review_checks.get(key) is True for key in required_approvals)
    )
    if review.get("status") == "approved" and not review_matches_revision:
        visual_status = "stale"
    elif review_matches_revision:
        visual_status = "approved"
    else:
        visual_status = "pending"

    if technical_status == "fail":
        acceptance_status = "fail"
    elif review_matches_revision:
        acceptance_status = "pass"
    else:
        acceptance_status = "pending-visual-review"

    required_visual_checks = [
        "identity-preserved-against-reference",
        "face-safe-in-central-80-percent-and-circular-crop",
        "readable-at-small-size",
        "natural-skin-and-editorial-brand-consistency",
        "no-unintended-text-watermark-instrument-or-generated-logo",
        "master-has-no-verification-badge",
    ]
    limitations = [] if review_matches_revision else [
        "پذیرش نهایی نیازمند مشاهده و تأیید بازبین برای هویت، قاب، متن و خوانایی است؛ این موارد با QC فنی اثبات نمی‌شوند."
    ]

    return {
        "schema_version": "1.0",
        "checked_at": utc_now(),
        "artist_id": metadata.get("artist_id"),
        "slug": metadata.get("slug"),
        "name_fa": metadata.get("name_fa"),
        "name_en": metadata.get("name_en"),
        "revision": metadata.get("revision"),
        "verification_status": verification.get("status"),
        "technical_status": technical_status,
        "visual_review_status": visual_status,
        "acceptance_status": acceptance_status,
        "checks": checks,
        "required_visual_checks": required_visual_checks,
        "files": files,
        "limitations": limitations,
        "note": "Technical QC does not perform facial recognition and never infers identity or verification status.",
    }
