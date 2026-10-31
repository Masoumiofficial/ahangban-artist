"""لایهٔ برند پروفایل هنرمند: مدالیون پرتره، نام فارسی/لاتین، حلقه، لوگو و نشان.

هیچ متن، لوگو یا نشانِ ساخته‌شده توسط مدل تصویرساز روی خروجی گذاشته نمی‌شود. لایهٔ نام
با رندر SVG و همان رندرگر ``resvg`` ساخته می‌شود که لوگو هم با آن رندر می‌گردد؛ به این
ترتیب shaping راست‌به‌چپ، نیم‌فاصله، ارقام فارسی و کشیدگی حروف درست و قطعی می‌ماند.
فقط فونت‌های پین‌شده در مخزن به رندرگر داده می‌شوند (``skip_system_fonts``) تا نتیجه
روی هر سیستمی بایت‌به‌بایت یکسان بماند.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import resvg_py
from PIL import Image, ImageDraw

from .errors import ValidationError
from .settings import Settings

SUPERSAMPLE = 2
MASK_SUPERSAMPLE = 4


@dataclass(frozen=True)
class TextLine:
    role: str
    text: str
    size: int
    weight: int
    baseline_y: int
    color: str
    alpha: int
    letter_spacing: int
    bbox: tuple[int, int, int, int]


def safe_circle(settings: Settings) -> tuple[tuple[float, float], float]:
    """مرکز و شعاع دایرهٔ امن مرکزی، مشتق‌شده از ``canvas.safe_area_ratio``."""
    center = (settings.width / 2, settings.height / 2)
    radius = min(settings.width, settings.height) * settings.safe_area_ratio / 2
    return center, radius


def rect_inside_circle(bbox: tuple[int, int, int, int], center: tuple[float, float], radius: float) -> bool:
    """محافظه‌کارانه: هر چهار گوشهٔ جعبه باید داخل دایره بمانند (برای متن و جعبه‌های مستطیل)."""
    left, top, right, bottom = bbox
    center_x, center_y = center
    return all(
        (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2
        for x, y in ((left, top), (right, top), (left, bottom), (right, bottom))
    )


def content_containment(settings: Settings, geometry: dict[str, Any] | None = None) -> tuple[tuple[float, float], float]:
    """دایره‌ای که عناصر متنی باید داخلش بمانند.

    برای حالت مدالیون، محدود‌کنندهٔ واقعی حلقهٔ بیرونی منتهی content_padding است، نه
    صرفاً دایرهٔ امن؛ به این دلیل هیچ نامی هرگز از خط حلقه عبور نمی‌کند.
    """
    center, safe_radius = safe_circle(settings)
    if settings.profile_layout.get("portrait_fit", "medallion") == "full":
        return center, safe_radius
    geometry = geometry if geometry is not None else profile_geometry(settings)
    padding = int(settings.profile_layout.get("content_padding", 24))
    disc_radius = float(geometry["medallion"]["outer_radius"]) - padding
    return center, min(safe_radius, disc_radius)


def disc_inside_circle(
    disc_center: tuple[float, float], disc_radius: float, center: tuple[float, float], radius: float
) -> bool:
    """یک قرص فقط وقتی داخل قرس دیگر است که فاصلهٔ مراکز + شعاع آن، از شعاع مجاز کمتر باشد."""
    distance = ((disc_center[0] - center[0]) ** 2 + (disc_center[1] - center[1]) ** 2) ** 0.5
    return distance + disc_radius <= radius + 1e-6


def boxes_overlap(left: tuple[int, int, int, int], right: tuple[int, int, int, int], pad: int = 0) -> bool:
    return not (
        left[2] + pad <= right[0]
        or right[2] + pad <= left[0]
        or left[3] + pad <= right[1]
        or right[3] + pad <= left[1]
    )


def profile_geometry(settings: Settings) -> dict[str, Any]:
    """هندسهٔ چیدمان پروفایل؛ تنها جایی که مختصات از تنظیمات مشتق می‌شود.

    دایرهٔ پرتره یک قرص است، نه یک مستطیل؛ بنابراین با «قرص داخل قرص» سنجیده می‌شود،
    در حالی‌که بلوک نام و لوگو (جعبهٔ مستطیل‌مانند) محافظه‌کارانه با گوشه‌ها سنجیده
    می‌شوند. هر دو در برابر دایرهٔ امن مرکزی کنترل می‌شوند.
    """
    profile = settings.profile_layout
    canvas = settings.width
    fit = str(profile.get("portrait_fit", "medallion"))
    cfg = profile.get("portrait_medallion", {})
    center = (settings.width / 2, settings.height / 2)
    safe_radius = min(settings.width, settings.height) * settings.safe_area_ratio / 2
    center_x = int(cfg.get("center_x", canvas // 2))
    center_y = int(cfg.get("center_y", canvas // 2))
    radius = int(cfg.get("diameter", 740)) // 2
    disc_center = (center_x, center_y)

    if fit == "full":
        bbox = (0, 0, settings.width, settings.height)
        ring = None
        outer_radius = min(settings.width, settings.height) / 2
    else:
        ring_width = int(cfg.get("ring_width", 0))
        ring_gap = int(cfg.get("ring_gap", 0))
        ring = None
        outer_radius = radius
        if ring_width > 0:
            ring = {
                "center_x": center_x,
                "center_y": center_y,
                "stroke_center_radius": radius + ring_gap + ring_width / 2,
                "outer_radius": radius + ring_gap + ring_width,
                "width_px": ring_width,
                "gap_px": ring_gap,
                "color": settings.color(str(cfg.get("ring_color", "brand_blue"))),
                "alpha": int(cfg.get("ring_alpha", 255)),
            }
            outer_radius = radius + ring_gap + ring_width
        bbox = (center_x - outer_radius, center_y - outer_radius, center_x + outer_radius, center_y + outer_radius)
        if not disc_inside_circle(disc_center, outer_radius, center, safe_radius):
            raise ValidationError(
                "مدالیون پرتره یا حلقهٔ آن از دایرهٔ امن بیرون می‌زند؛ "
                f"قرص به شعاع {outer_radius}px در مرکز ({center_x},{center_y}) با "
                f"دایرهٔ امن {safe_radius}px سازگار نیست."
            )

    badge_cfg = dict(settings.badge_layout)
    badge_cfg.update(profile.get("verification_badge", {}))
    anchor = str(badge_cfg.pop("anchor", ""))
    badge_size = int(badge_cfg["size"])
    if anchor in {"medallion_edge", "disc_edge"} and fit != "full":
        # مرکز نشان روی شعاعی از قرص بیرونی، با زاویهٔ مشخص (درجه، ساعت‌گرد از ۳)
        # و فاصلهٔ inset از لبه قرار می‌گیرد؛ جای ثابت نشان هیچ‌وقت با بلوک نام
        # برخورد نمی‌کند و همیشه داخل دایرهٔ امن می‌ماند.
        angle = float(badge_cfg.pop("angle_deg", 0.0))
        inset = float(badge_cfg.pop("inset", 0))
        anchor_radius = outer_radius - inset
        badge_cfg.clear()
        badge_cfg["size"] = badge_size
        badge_cfg["x"] = round(center_x + anchor_radius * math.cos(math.radians(angle)) - badge_size / 2)
        badge_cfg["y"] = round(center_y + anchor_radius * math.sin(math.radians(angle)) - badge_size / 2)
    else:
        badge_cfg.setdefault("x", settings.badge_layout["x"])
        badge_cfg.setdefault("y", settings.badge_layout["y"])

    return {
        "fit": fit,
        "medallion": {
            "center_x": center_x,
            "center_y": center_y,
            "radius": radius,
            "outer_radius": outer_radius,
            "focus_y": float(cfg.get("focus_y", 0.5)),
        },
        "portrait_bbox": list(bbox),
        "ring": ring,
        "badge": badge_cfg,
        "logo": dict(profile.get("logo", settings.logo_layout)),
    }


def _raster_svg(settings: Settings, svg: str) -> Image.Image:
    canvas = settings.width
    size = canvas * SUPERSAMPLE
    try:
        rendered = resvg_py.svg_to_bytes(
            svg_string=svg,
            width=size,
            height=size,
            font_files=[str(path) for path in sorted(settings.font_files.values())],
            skip_system_fonts=True,
        )
    except Exception as exc:  # resvg does not expose typed errors
        raise ValidationError(f"رندر لایهٔ برند SVG شکست خورد: {exc}") from exc
    with Image.open(io.BytesIO(rendered)) as image:
        image.load()
        layer = image.convert("RGBA")
    if layer.size != (canvas, canvas):
        layer = layer.resize((canvas, canvas), Image.Resampling.LANCZOS)
    return layer


def _svg_document(settings: Settings, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{settings.width}" height="{settings.height}"'
        f' viewBox="0 0 {settings.width} {settings.height}">{body}</svg>'
    )


def _line_element(settings: Settings, line: TextLine) -> str:
    spacing = f' letter-spacing="{line.letter_spacing}"' if line.letter_spacing else ""
    return (
        f'<text x="{settings.width / 2:.0f}" y="{line.baseline_y}"'
        f' font-family="{xml_escape(settings.font_family)}"'
        f' font-weight="{line.weight}" font-size="{line.size}"'
        f' fill="{line.color}" fill-opacity="{round(line.alpha / 255, 4)}"'
        f' text-anchor="middle"{spacing}>{xml_escape(line.text)}</text>'
    )


def _style(settings: Settings, role: str) -> tuple[str, int, int, int, int, int]:
    cfg = settings.profile_layout[role]
    return (
        settings.color(str(cfg.get("color", "white"))),
        int(cfg.get("alpha", 255)),
        int(cfg.get("weight", 400)),
        int(cfg["baseline_y"]),
        int(cfg.get("letter_spacing", 0)),
        int(cfg["size"]),
    )


def fit_line(settings: Settings, text: str, role: str, containment: tuple[tuple[float, float], float] | None = None) -> TextLine:
    """کمینه‌کردن اندازه تا جایی که جعبهٔ جوهری نام کامل داخل دایرهٔ محتوا بماند."""
    color, alpha, weight, baseline_y, base_spacing, max_size = _style(settings, role)
    center, radius = containment if containment is not None else content_containment(settings)
    if settings.profile_layout[role].get("uppercase") is True:
        text = text.upper()
    min_size = int(settings.profile_layout[role].get("min_size", max(18, round(max_size * 0.55))))
    if min_size > max_size:
        raise ValidationError(f"کمینهٔ اندازهٔ «{role}» از اندازهٔ اصلی بزرگ‌تر است.")

    for size in range(max_size, min_size - 1, -1):
        spacing = round(base_spacing * size / max_size) if base_spacing else 0
        line = TextLine(role, text, size, weight, baseline_y, color, alpha, spacing, (0, 0, 0, 0))
        layer = _raster_svg(settings, _svg_document(settings, _line_element(settings, line)))
        bbox = layer.getchannel("A").getbbox()
        if bbox is None:
            raise ValidationError(f"نام «{text}» هیچ گلیف قابل‌مشاهده‌ای رندر نکرد.")
        if rect_inside_circle(bbox, center, radius):
            return TextLine(role, text, size, weight, baseline_y, color, alpha, spacing, bbox)

    raise ValidationError(
        f"«{text}» حتی در کمینهٔ اندازهٔ {min_size}px داخل دایرهٔ امن به شعاع {radius:.0f}px "
        "جا نمی‌شود؛ نام نمایشی را کوتاه‌تر کنید یا چیدمان پروفایل را آگاهانه تغییر دهید."
    )


def _divider_rect(settings: Settings) -> dict[str, Any] | None:
    cfg = settings.profile_layout.get("divider")
    if not cfg or not cfg.get("enabled", True):
        return None
    width = int(cfg["width"])
    height = int(cfg["height"])
    return {
        "x": round(settings.width / 2 - width / 2),
        "y": int(cfg["center_y"]) - round(height / 2),
        "width": width,
        "height": height,
        "fill": settings.color(str(cfg.get("color", "brand_blue"))),
        "opacity": round(int(cfg.get("alpha", 255)) / 255, 4),
        "rx": int(cfg.get("radius", 0)),
    }


def render_name_layer(
    settings: Settings,
    name_fa: str,
    name_en: str,
    geometry: dict[str, Any] | None = None,
) -> tuple[Image.Image, dict[str, Any]]:
    """لایهٔ شفاف هم‌اندازهٔ قاب شامل نام فارسی، خط جداکننده و نام لاتین، به‌همراه اطلاعات هندسی."""
    if not str(name_fa).strip() or not str(name_en).strip():
        raise ValidationError("برای چیدمان پروفایل، نام فارسی و لاتین هر دو الزامی‌اند.")
    if geometry is None:
        geometry = profile_geometry(settings)
    center, radius = content_containment(settings, geometry)
    lines = [
        fit_line(settings, str(name_fa), "name_fa", (center, radius)),
        fit_line(settings, str(name_en), "name_en", (center, radius)),
    ]

    body = [_line_element(settings, line) for line in lines]
    divider = _divider_rect(settings)
    if divider:
        body.append(
            f'<rect x="{divider["x"]}" y="{divider["y"]}" width="{divider["width"]}"'
            f' height="{divider["height"]}" fill="{divider["fill"]}"'
            f' fill-opacity="{divider["opacity"]}" rx="{divider["rx"]}"/>'
        )
    layer = _raster_svg(settings, _svg_document(settings, "".join(body)))
    block = layer.getchannel("A").getbbox()
    if block is None:
        raise ValidationError("لایهٔ نام خالی است؛ حداقل نام فارسی باید دیده شود.")
    # جعبهٔ اشتراکی دو خطِ با عرض متفاوت می‌تواند گوشه‌اش بیرون دایره بیفتد بدون آنکه
    # هیچ گلیفی بیرون رفته باشد؛ بنابراین هر عنصر جدا سنجیده می‌شود.
    elements = [(f"line:{line.role}", line.bbox) for line in lines]
    if divider:
        elements.append(("divider", (divider["x"], divider["y"], divider["x"] + divider["width"], divider["y"] + divider["height"])))
    outside = [name for name, box in elements if not rect_inside_circle(box, center, radius)]
    if outside:
        raise ValidationError(
            "این عناصر نام از دایرهٔ امن بیرون می‌زنند: " + ", ".join(outside) + "؛ چیدمان را بررسی کنید."
        )

    info: dict[str, Any] = {
        "font_family": settings.font_family,
        "containment_circle": {"center_x": center[0], "center_y": center[1], "radius": radius},
        "lines": [
            {
                "role": line.role,
                "text": line.text,
                "size_px": line.size,
                "weight": line.weight,
                "baseline_y": line.baseline_y,
                "letter_spacing_px": line.letter_spacing,
                "ink_bbox": list(line.bbox),
                "shrink_applied": line.size != _style(settings, line.role)[5],
            }
            for line in lines
        ],
        "divider": divider,
        "block_bbox": list(block),
        "element_bboxes": {name: list(box) for name, box in elements},
    }
    return layer, info


def _circular_mask(size: int) -> Image.Image:
    large = Image.new("L", (size * MASK_SUPERSAMPLE, size * MASK_SUPERSAMPLE), 0)
    draw = ImageDraw.Draw(large)
    draw.ellipse((0, 0, size * MASK_SUPERSAMPLE - 1, size * MASK_SUPERSAMPLE - 1), fill=255)
    return large.resize((size, size), Image.Resampling.LANCZOS)


def _cover_crop(
    image: Image.Image, box: tuple[int, int, int, int], target: int, focus_y: float
) -> Image.Image:
    """متناسب‌سازی و برش جعبهٔ محتوا به‌گونه‌ای که دایره کاملاً با عکس پر شود (بدون کشیدگی)."""
    left, top, right, bottom = box
    content = image.crop((left, top, right, bottom))
    content_width, content_height = content.size
    if content_width <= 0 or content_height <= 0:
        raise ValidationError("جعبهٔ محتوای پرتره معتبر نیست.")
    scale = target / min(content_width, content_height)
    scaled = content.resize(
        (max(target, round(content_width * scale)), max(target, round(content_height * scale))),
        Image.Resampling.LANCZOS,
    )
    offset_x = (scaled.width - target) // 2
    max_offset_y = max(0, scaled.height - target)
    offset_y = min(max_offset_y, max(0, round(max_offset_y * focus_y)))
    return scaled.crop((offset_x, offset_y, offset_x + target, offset_y + target))


def _portrait_layer(
    settings: Settings, master: Image.Image, content_box: tuple[int, int, int, int], geometry: dict[str, Any]
) -> Image.Image:
    canvas = Image.new("RGBA", (settings.width, settings.height), (0, 0, 0, 0))
    if geometry["fit"] == "full":
        canvas.alpha_composite(master.convert("RGBA"))
        return canvas
    medallion = geometry["medallion"]
    diameter = medallion["radius"] * 2
    portrait = _cover_crop(master.convert("RGB"), content_box, diameter, medallion["focus_y"]).convert("RGBA")
    canvas.paste(
        portrait,
        (medallion["center_x"] - medallion["radius"], medallion["center_y"] - medallion["radius"]),
        _circular_mask(diameter),
    )
    return canvas


def _scrim_layer(settings: Settings) -> Image.Image:
    """سایهٔ یکنواخت زیر بلوک نام، فقط به‌عنوان لایهٔ گرافیکی برند (نه تغییر در پرتره)."""
    cfg = settings.profile_layout.get("scrim")
    if not cfg or not cfg.get("enabled", True):
        return Image.new("RGBA", (settings.width, settings.height), (0, 0, 0, 0))
    top_y = int(cfg["top_y"])
    bottom_y = int(cfg["bottom_y"])
    max_alpha = int(cfg.get("max_alpha", 220))
    if bottom_y <= top_y:
        raise ValidationError("top_y scrim باید کوچک‌تر از bottom_y باشد.")
    hex_color = settings.color(str(cfg.get("color", "ink"))).lstrip("#")
    red, green, blue = (int(hex_color[index : index + 2], 16) for index in (0, 2, 4))

    alpha = Image.new("L", (1, settings.height), 0)
    for y in range(settings.height):
        if y <= top_y:
            value = 0
        elif y >= bottom_y:
            value = max_alpha
        else:
            value = round(max_alpha * (y - top_y) / (bottom_y - top_y))
        alpha.putpixel((0, y), value)
    solid = Image.new("RGBA", (settings.width, settings.height), (red, green, blue, 0))
    solid.putalpha(alpha.resize((settings.width, settings.height)))
    return solid


def _badge_box(badge_cfg: dict[str, Any]) -> tuple[int, int, int, int]:
    size = int(badge_cfg["size"])
    x = int(badge_cfg.get("x", 0))
    y = int(badge_cfg.get("y", 0))
    return (x, y, x + size, y + size)


def _ring_svg(geometry: dict[str, Any]) -> str:
    ring = geometry.get("ring")
    if not ring:
        return ""
    return (
        f'<circle cx="{ring["center_x"]}" cy="{ring["center_y"]}" r="{ring["stroke_center_radius"]}"'
        f' fill="none" stroke="{ring["color"]}" stroke-width="{ring["width_px"]}"'
        f' stroke-opacity="{round(ring["alpha"] / 255, 4)}"/>'
    )


def compose_profile(
    master: Image.Image,
    settings: Settings,
    artist_dir: Path,
    verified: bool,
    name_fa: str,
    name_en: str,
    content_box: tuple[int, int, int, int] | None = None,
) -> tuple[Image.Image, dict[str, Any]]:
    """ساخت خروجی پروفایل از master؛ خودِ master هرگز تغییر نمی‌کند."""
    from .images import _official_logo, _verification_badge

    if master.size != (settings.width, settings.height):
        raise ValidationError("پرترهٔ اصلی پیش از ترکیب باید 1080×1080 باشد.")

    geometry = profile_geometry(settings)
    center, radius = safe_circle(settings)

    box = content_box or (0, 0, settings.width, settings.height)
    canvas = Image.new("RGBA", (settings.width, settings.height), settings.background)
    canvas.alpha_composite(_portrait_layer(settings, master, box, geometry))
    canvas.alpha_composite(_scrim_layer(settings))

    name_layer, name_info = render_name_layer(settings, name_fa, name_en, geometry)
    canvas.alpha_composite(name_layer)
    if geometry["ring"]:
        ring_layer = _raster_svg(settings, _svg_document(settings, _ring_svg(geometry)))
        canvas.alpha_composite(ring_layer)

    badge_geometry = geometry["badge"]
    name_boxes = [tuple(box) for box in name_info["element_bboxes"].values()]
    if any(boxes_overlap(box, _badge_box(badge_geometry), pad=8) for box in name_boxes):
        raise ValidationError(
            "نشان تأیید با بلوک نام برخورد دارد؛ verification_badge.angle_deg، اندازهٔ نشان "
            "یا خط زمینهٔ نام را آگاهانه تغییر دهید."
        )

    logo, logo_position = _official_logo(settings, geometry["logo"])
    canvas.alpha_composite(logo, logo_position)
    logo_bbox = (logo_position[0], logo_position[1], logo_position[0] + logo.width, logo_position[1] + logo.height)
    content_center, content_radius = content_containment(settings, geometry)
    if not rect_inside_circle(logo_bbox, content_center, content_radius):
        raise ValidationError("لوگوی رسمی داخل دایرهٔ محتوای پروفایل (حلقه منهای حاشیه) جای نمی‌گیرد.")

    badge_info: dict[str, Any] = {"applied": False, "bbox": None}
    if verified:
        badge, badge_position = _verification_badge(settings, artist_dir, layout=badge_geometry)
        canvas.alpha_composite(badge, badge_position)
        badge_info = {
            "applied": True,
            "position": list(badge_position),
            "bbox": [
                badge_position[0],
                badge_position[1],
                badge_position[0] + badge.width,
                badge_position[1] + badge.height,
            ],
        }

    info: dict[str, Any] = {
        "design_version": settings.design_version,
        "safe_circle": {"center_x": center[0], "center_y": center[1], "radius": radius},
        "portrait": {"bbox": geometry["portrait_bbox"], "ring": geometry["ring"], "fit": geometry["fit"]},
        "names": name_info,
        "logo": {"bbox": list(logo_bbox), "visible_width": logo.width, "position": list(logo_position)},
        "badge": badge_info,
    }
    return canvas.convert("RGB"), info
