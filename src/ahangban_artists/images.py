from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Any

import resvg_py
from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageOps, UnidentifiedImageError

from .errors import ValidationError
from .settings import Settings
from .utils import sha256_file


FORMAT_SUFFIXES = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "TIFF": ".tiff"}


@lru_cache(maxsize=1)
def srgb_profile_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _open_image(path: str | Path) -> Image.Image:
    try:
        return Image.open(path)
    except FileNotFoundError as exc:
        raise ValidationError(f"تصویر پیدا نشد: {path}") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError(f"تصویر سالم و پشتیبانی‌شده نیست: {path} ({exc})") from exc


def analyze_reference(path: str | Path, settings: Settings) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise ValidationError(f"تصویر مرجع پیدا نشد: {source}")

    with _open_image(source) as image:
        image.load()
        image_format = str(image.format or "").upper()
        if image_format not in settings.supported_formats:
            supported = ", ".join(sorted(settings.supported_formats))
            raise ValidationError(f"فرمت مرجع «{image_format or 'unknown'}» پشتیبانی نمی‌شود؛ فرمت‌های مجاز: {supported}")
        width, height = image.size
        if width < 1 or height < 1:
            raise ValidationError("ابعاد تصویر مرجع معتبر نیست.")
        mode = image.mode
        has_alpha = "A" in mode or "transparency" in image.info
        has_icc = bool(image.info.get("icc_profile"))
        grayscale = mode in {"1", "L", "LA", "I", "I;16"}
        if mode == "RGB":
            red, green, blue = image.split()
            grayscale = (
                ImageChops.difference(red, green).getbbox() is None
                and ImageChops.difference(red, blue).getbbox() is None
            )

    warnings: list[str] = []
    if min(width, height) < settings.minimum_reference_edge:
        warnings.append(
            "ضلع کوتاه مرجع کمتر از 1080 پیکسل است؛ جزئیات هویتی جدید نباید در تولید پرتره اختراع شود."
        )
    if width != height:
        warnings.append("مرجع مربع نیست؛ قاب و حاشیهٔ امن باید در تولید پرتره کنترل شوند.")
    if not has_icc:
        warnings.append("پروفایل رنگ مرجع مشخص نیست؛ فایل اصلی دست‌نخورده می‌ماند و پروفایل خروجی جداگانه کنترل می‌شود.")

    return {
        "original_filename": source.name,
        "format": image_format,
        "suffix": FORMAT_SUFFIXES[image_format],
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 6),
        "mode": mode,
        "has_alpha": has_alpha,
        "has_icc_profile": has_icc,
        "appears_grayscale": grayscale,
        "bytes": source.stat().st_size,
        "sha256": sha256_file(source),
        "warnings": warnings,
    }


def _convert_to_srgb(image: Image.Image, background: str) -> Image.Image:
    """Apply geometry/color conversion only; never retouch or synthesize facial details."""
    image = ImageOps.exif_transpose(image)
    icc = image.info.get("icc_profile")

    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        base = Image.new("RGBA", rgba.size, background)
        return Image.alpha_composite(base, rgba).convert("RGB")

    if icc:
        try:
            source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            target_profile = ImageCms.ImageCmsProfile(io.BytesIO(srgb_profile_bytes()))
            return ImageCms.profileToProfile(image, source_profile, target_profile, outputMode="RGB")
        except (OSError, ValueError) as exc:
            raise ValidationError(f"تبدیل پروفایل رنگ تصویر ممکن نبود: {exc}") from exc

    return image.convert("RGB")


def prepare_master(path: str | Path, settings: Settings) -> tuple[Image.Image, dict[str, Any]]:
    source = Path(path)
    with _open_image(source) as opened:
        opened.load()
        source_format = str(opened.format or "").upper()
        if source_format not in settings.supported_formats:
            raise ValidationError(f"فرمت پرترهٔ ورودی «{source_format or 'unknown'}» پشتیبانی نمی‌شود.")
        original_size = opened.size
        converted = _convert_to_srgb(opened, settings.background)

    original_width, original_height = converted.size
    scale = min(settings.width / original_width, settings.height / original_height)
    resized_width = max(1, round(original_width * scale))
    resized_height = max(1, round(original_height * scale))
    if converted.size == (resized_width, resized_height):
        resized = converted.copy()
    else:
        resized = converted.resize((resized_width, resized_height), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (settings.width, settings.height), settings.background)
    offset_x = (settings.width - resized_width) // 2
    offset_y = (settings.height - resized_height) // 2
    canvas.paste(resized, (offset_x, offset_y))

    transform = {
        "operation": "exif-orientation, sRGB conversion, proportional contain, centered charcoal canvas",
        "generative_retouching_by_pipeline": False,
        "source_format": source_format,
        "source_width": original_size[0],
        "source_height": original_size[1],
        "resized_width": resized_width,
        "resized_height": resized_height,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "scale": round(scale, 8),
        "upscaled": scale > 1,
        "background_fill": settings.background,
    }
    return canvas, transform


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, icc_profile=srgb_profile_bytes())
    return buffer.getvalue()


def load_png(path: str | Path) -> Image.Image:
    with _open_image(path) as image:
        image.load()
        if image.format != "PNG":
            raise ValidationError(f"فایل خروجی PNG نیست: {path}")
        return image.copy()


def _official_logo(settings: Settings) -> tuple[Image.Image, tuple[int, int]]:
    if settings.logo_path.suffix.lower() == ".svg":
        try:
            rendered = resvg_py.svg_to_bytes(
                svg_path=str(settings.logo_path),
                width=2080,
                skip_system_fonts=True,
            )
        except Exception as exc:
            raise ValidationError(f"رندر لوگوی رسمی SVG شکست خورد: {exc}") from exc
        with Image.open(io.BytesIO(rendered)) as source:
            source.load()
            logo = source.convert("RGBA")
    else:
        with _open_image(settings.logo_path) as source:
            source.load()
            logo = source.convert("RGBA")

    layout = settings.logo_layout
    if layout.get("trim_transparent_padding", True):
        bounds = logo.getchannel("A").getbbox()
        if bounds is None:
            raise ValidationError("لوگوی رسمی هیچ پیکسل قابل‌مشاهده‌ای ندارد.")
        logo = logo.crop(bounds)

    visible_width = int(layout["visible_width"])
    visible_height = max(1, round(logo.height * visible_width / logo.width))
    logo = logo.resize((visible_width, visible_height), Image.Resampling.LANCZOS)
    position = (int(layout["x"]), int(layout["y"]))
    right = position[0] + logo.width
    bottom = position[1] + logo.height
    if position[0] < 0 or position[1] < 0 or right > settings.width or bottom > settings.height:
        raise ValidationError("چیدمان لوگو خارج از قاب قرار می‌گیرد.")
    return logo, position


def _verification_badge(settings: Settings, artist_dir: Path) -> tuple[Image.Image, tuple[int, int]]:
    layout = settings.badge_layout
    size = int(layout["size"])
    supersampled_size = size * 4
    badge = Image.new("RGBA", (supersampled_size, supersampled_size), (0, 0, 0, 0))

    for layer_name in settings.badge_layer_names:
        layer_path = artist_dir / "badges" / layer_name
        if not layer_path.is_file():
            raise ValidationError(f"لایهٔ مستقل نشان پیدا نشد: {layer_path}")
        try:
            rendered = resvg_py.svg_to_bytes(
                svg_path=str(layer_path),
                width=supersampled_size,
                height=supersampled_size,
                skip_system_fonts=True,
            )
        except Exception as exc:
            raise ValidationError(f"رندر لایهٔ SVG نشان شکست خورد: {layer_path} ({exc})") from exc
        with Image.open(io.BytesIO(rendered)) as layer_image:
            layer = layer_image.convert("RGBA")
            badge = Image.alpha_composite(badge, layer)

    badge = badge.resize((size, size), Image.Resampling.LANCZOS)
    position = (int(layout["x"]), int(layout["y"]))
    if position[0] < 0 or position[1] < 0 or position[0] + size > settings.width or position[1] + size > settings.height:
        raise ValidationError("چیدمان نشان تأیید خارج از قاب قرار می‌گیرد.")
    return badge, position


def compose_final(master: Image.Image, settings: Settings, artist_dir: Path, verified: bool) -> Image.Image:
    if master.size != (settings.width, settings.height):
        raise ValidationError("پرترهٔ اصلی پیش از ترکیب باید 1080×1080 باشد.")
    final = master.convert("RGB").copy()
    logo, logo_position = _official_logo(settings)
    final.paste(logo, logo_position, logo)
    if verified:
        badge, badge_position = _verification_badge(settings, artist_dir)
        final.paste(badge, badge_position, badge)
    return final


def circular_preview(final: Image.Image) -> Image.Image:
    width, height = final.size
    scale = 4
    mask_large = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(mask_large)
    draw.ellipse((0, 0, width * scale - 1, height * scale - 1), fill=255)
    mask = mask_large.resize((width, height), Image.Resampling.LANCZOS)
    preview = final.convert("RGBA")
    preview.putalpha(mask)
    return preview


def images_equal(left: Image.Image, right: Image.Image) -> bool:
    if left.size != right.size or left.mode != right.mode:
        return False
    return ImageChops.difference(left, right).getbbox() is None


def inspect_png(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        with _open_image(source) as image:
            if image.format != "PNG":
                raise ValidationError(f"فرمت فایل PNG نیست: {source}")
            image.verify()
        with _open_image(source) as image:
            image.load()
            icc_profile = image.info.get("icc_profile")
            profile_description = ""
            if icc_profile:
                try:
                    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
                    profile_description = ImageCms.getProfileDescription(profile).strip()
                except (OSError, ValueError):
                    profile_description = ""
            normalized_description = "".join(profile_description.lower().split())
            return {
                "path": str(source),
                "format": image.format,
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "has_icc_profile": bool(icc_profile),
                "srgb_profile": "srgb" in normalized_description,
                "icc_profile_description": profile_description,
                "bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
    except (OSError, SyntaxError) as exc:
        raise ValidationError(f"PNG سالم نیست: {source} ({exc})") from exc
