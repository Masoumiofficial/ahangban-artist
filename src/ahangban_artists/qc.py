from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import ValidationError
from .images import (
    _official_logo,
    brand_inputs,
    circular_preview,
    compose_final,
    images_equal,
    inspect_png,
    load_png,
)
from .profile import (
    content_containment,
    disc_inside_circle,
    profile_geometry,
    rect_inside_circle,
    render_name_layer,
    safe_circle,
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
    font_results = []
    recorded_fonts = assets.get("font_sha256", {})
    for font_path in settings.font_files.values():
        expected_font = recorded_fonts.get(font_path.name)
        font_results.append(bool(expected_font) and sha256_file(font_path) == expected_font)
    check(
        "fixed_brand_assets_unmodified",
        logo_ok and all(badge_results) and all(font_results),
        "هش فایل رسمی لوگو، لایه‌های SVG مستقل و فونت‌های پین‌شده با فرادادهٔ پروژه مقایسه شد؛ "
        f"لوگو={logo_ok}، نشان‌ها={all(badge_results)}، فونت‌ها={all(font_results) if font_results else 'بدون ثبت'}",
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
            expected_final = compose_final(
                master_image, settings, artist_dir, verified, **brand_inputs(settings, metadata)
            )
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
        if settings.profile_enabled:
            center, radius = safe_circle(settings)
            geometry = profile_geometry(settings)
            content_center, content_radius = content_containment(settings, geometry)
            logo, logo_position = _official_logo(settings, geometry["logo"])
            # مدالیون یک قرص است، نه یک مستطیل؛ با قرص‌داخل‌قرص سنجیده می‌شود
            medallion = geometry["medallion"]
            boxes = {
                "لوگو": (
                    (
                        logo_position[0],
                        logo_position[1],
                        logo_position[0] + logo.width,
                        logo_position[1] + logo.height,
                    ),
                    content_center,
                    content_radius,
                ),
            }
            if verified:
                badge_size = int(geometry["badge"]["size"])
                badge_x = int(geometry["badge"].get("x", settings.badge_layout["x"]))
                badge_y = int(geometry["badge"].get("y", settings.badge_layout["y"]))
                boxes["نشان تأیید"] = ((badge_x, badge_y, badge_x + badge_size, badge_y + badge_size), center, radius)
            outside = [name for name, (box, box_center, box_radius) in boxes.items() if not rect_inside_circle(box, box_center, box_radius)]
            if not disc_inside_circle((medallion["center_x"], medallion["center_y"]), medallion["outer_radius"], center, radius):
                outside.append("مدالیون پرتره")
            check(
                "brand_layers_inside_circular_crop",
                not outside,
                f"دایرهٔ امن {radius:.0f}px و دایرهٔ محتوا {content_radius:.0f}px به‌صورت محافظه‌کارانه بررسی شد؛ "
                + ("بیرون‌زده: " + ", ".join(outside) if outside else "هیچ المانی بیرون از دایرهٔ خودش نیست."),
            )
        else:
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

    if settings.profile_enabled:
        try:
            geometry = profile_geometry(settings)
            center, radius = content_containment(settings, geometry)
            _, name_info = render_name_layer(settings, str(metadata["name_fa"]), str(metadata["name_en"]), geometry)
            recorded = metadata.get("profile", {}).get("names", {})
            same_geometry = (
                recorded.get("lines") == name_info["lines"]
                and recorded.get("element_bboxes") == name_info["element_bboxes"]
            )
            block = tuple(name_info["block_bbox"])
            inside = all(
                rect_inside_circle(tuple(box), center, radius) for box in name_info["element_bboxes"].values()
            )
            fa_line = next(item for item in name_info["lines"] if item["role"] == "name_fa")
            fa_height = fa_line["ink_bbox"][3] - fa_line["ink_bbox"][1]
            readable = fa_height >= int(settings.profile_layout["name_fa"].get("min_size", 0)) * 0.6
            check(
                "name_layer_from_metadata_and_circle_safe",
                inside and same_geometry and readable,
                "بلوک نام بازتولید و با فراداده مقایسه شد؛ "
                f"bbox={list(block)}، داخل دایرهٔ امن={inside}، هم‌خوان با metadata={same_geometry}، "
                f"ارتفاع جوهری نام فارسی={fa_height}px (خوانا={readable})",
            )
        except (KeyError, ValueError, ValidationError) as exc:
            check("name_layer_from_metadata_and_circle_safe", False, str(exc))

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
    limitations: list[str] = []
    reference_info = metadata.get("reference", {})
    reference_edge = min(
        int(reference_info.get("width") or 0), int(reference_info.get("height") or 0)
    ) if reference_info.get("width") and reference_info.get("height") else 0
    if 0 < reference_edge < settings.minimum_reference_edge:
        limitations.append(
            f"وضوح مرجع اصلی {reference_edge}px است (کمینهٔ پیشنهادی {settings.minimum_reference_edge}px)؛ "
            "هر خروجی مشتق‌شده از همین مرجع، از جمله نسخه‌های بازتولیدشده بدون تغییر master، همچنان از "
            "منبع محدود می‌آید و این با QC یا باز‌نشر نسخه حذف نمی‌شود."
        )
    if settings.profile_enabled:
        shrink = [
            line
            for line in metadata.get("profile", {}).get("names", {}).get("lines", [])
            if line.get("shrink_applied")
        ]
        if shrink:
            limitations.append(
                "برای ماندن داخل دایرهٔ امن، اندازهٔ نام کاهش یافت ("
                + "، ".join(f"{item['role']}={item['size_px']}px" for item in shrink)
                + ")؛ این کاهش از خوانایی در اندازهٔ کوچک باید بصری تأیید شود."
            )
    transform = metadata.get("generation", {}).get("transform", {})
    if transform.get("upscaled"):
        limitations.append(
            "خروجی شامل upscale از master ورودی است؛ وضوح واقعی به منبع محدود است و این وضعیت با پذیرش بصری حذف نمی‌شود."
        )
    if not review_matches_revision:
        limitations.append(
            "پذیرش نهایی نیازمند مشاهده و تأیید بازبین برای هویت، قاب، متن و خوانایی است؛ این موارد با QC فنی اثبات نمی‌شوند."
        )

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
