from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ValidationError

EXPECTED_PALETTE = {
    "ink": "#111111",
    "charcoal": "#222222",
    "brand_blue": "#185ADB",
    "verification_gold": "#D4AF37",
    "white": "#FFFFFF",
}

SUPPORTED_DESIGN_VERSIONS = {"1.1.0"}
REQUIRED_PROFILE_KEYS = (
    "portrait_medallion",
    "name_fa",
    "name_en",
    "logo",
)


@dataclass(frozen=True)
class Settings:
    root: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, root: str | Path) -> "Settings":
        root_path = Path(root).expanduser().resolve()
        config_path = root_path / "brand-kit" / "cover-system.json"
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValidationError(f"فایل تنظیمات سیستم پیدا نشد: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValidationError(f"فایل تنظیمات JSON معتبر نیست: {exc}") from exc

        settings = cls(root_path, data)
        settings._validate()
        return settings

    def _validate(self) -> None:
        canvas = self.data.get("canvas", {})
        if (canvas.get("width"), canvas.get("height")) != (1080, 1080):
            raise ValidationError("ابعاد قاب در تنظیمات باید دقیقاً 1080×1080 باشد.")
        if canvas.get("format") != "PNG" or canvas.get("color_space") != "sRGB":
            raise ValidationError("فرمت و فضای رنگ خروجی باید PNG و sRGB باشد.")
        if self.data.get("palette") != EXPECTED_PALETTE:
            raise ValidationError("پالت برند با مقادیر ثابت آهنگ‌بان ناسازگار است.")
        if self.data.get("design_version") not in SUPPORTED_DESIGN_VERSIONS:
            supported = ", ".join(sorted(SUPPORTED_DESIGN_VERSIONS))
            raise ValidationError(f"این نسخهٔ ابزار فقط design_version={supported} را پشتیبانی می‌کند.")

        for key in ("prompt", "canonical_badges", "artists_root"):
            if key not in self.data.get("paths", {}):
                raise ValidationError(f"مسیر «{key}» در تنظیمات تعریف نشده است.")
        if not self.prompt_path.is_file():
            raise ValidationError(f"پرامپت ثابت پیدا نشد: {self.prompt_path}")
        if not self.logo_path.is_file():
            raise ValidationError(f"فایل رسمی لوگو پیدا نشد: {self.logo_path}")
        for layer in self.badge_layer_names:
            path = self.canonical_badges_path / layer
            if not path.is_file():
                raise ValidationError(f"لایهٔ نشان پیدا نشد: {path}")
        if not isinstance(self.data.get("fonts", {}).get("files"), dict) or not self.font_files:
            raise ValidationError("هیچ فایل فونت پین‌شده‌ای در تنظیمات تعریف نشده است.")
        for weight, path in self.font_files.items():
            if not path.is_file():
                raise ValidationError(f"فایل فونت پین‌شده پیدا نشد (وزن {weight}): {path}")
        if not self.font_license_path.is_file():
            raise ValidationError(f"مجوز فونت پیدا نشد: {self.font_license_path}")
        if self.profile_enabled:
            missing = [key for key in REQUIRED_PROFILE_KEYS if key not in self.profile_layout]
            if missing:
                raise ValidationError(
                    "چیدمان پروفایل کامل نیست؛ کلیدهای لازم: " + ", ".join(missing)
                )
            ratio = float(canvas.get("safe_area_ratio", 1.0))
            if not 0.0 < ratio <= 1.0:
                raise ValidationError("safe_area_ratio باید عددی در بازهٔ ۰ تا ۱ باشد.")

    def path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValidationError(f"مسیر خارج از ریشهٔ پروژه مجاز نیست: {relative}") from exc
        return candidate

    @property
    def width(self) -> int:
        return int(self.data["canvas"]["width"])

    @property
    def height(self) -> int:
        return int(self.data["canvas"]["height"])

    @property
    def background(self) -> str:
        return str(self.data["canvas"]["background"])

    @property
    def safe_area_ratio(self) -> float:
        return float(self.data["canvas"].get("safe_area_ratio", 1.0))

    @property
    def design_version(self) -> str:
        return str(self.data["design_version"])

    @property
    def prompt_version(self) -> str:
        return str(self.data["prompt_version"])

    @property
    def artists_root(self) -> Path:
        return self.path(self.data["paths"]["artists_root"])

    @property
    def prompt_path(self) -> Path:
        return self.path(self.data["paths"]["prompt"])

    @property
    def canonical_badges_path(self) -> Path:
        return self.path(self.data["paths"]["canonical_badges"])

    @property
    def logo_path(self) -> Path:
        return self.path(self.data["layout"]["logo"]["source"])

    @property
    def logo_layout(self) -> dict[str, Any]:
        return self.data["layout"]["logo"]

    @property
    def badge_layout(self) -> dict[str, Any]:
        return self.data["layout"]["verification_badge"]

    @property
    def badge_layer_names(self) -> list[str]:
        return list(self.badge_layout["layers"])

    @property
    def profile_layout(self) -> dict[str, Any]:
        return self.data["layout"].get("profile", {})

    @property
    def profile_enabled(self) -> bool:
        return bool(self.profile_layout.get("enabled", False))

    @property
    def font_family(self) -> str:
        return str(self.data.get("fonts", {}).get("family", "Vazirmatn"))

    @property
    def font_license_path(self) -> Path:
        return self.path(str(self.data["fonts"]["license"]))

    @property
    def font_files(self) -> dict[int, Path]:
        files = self.data.get("fonts", {}).get("files", {})
        return {int(weight): self.path(str(relative)) for weight, relative in files.items()}

    @property
    def minimum_reference_edge(self) -> int:
        return int(self.data["reference"]["minimum_recommended_short_edge"])

    @property
    def supported_formats(self) -> set[str]:
        return set(self.data["reference"]["supported_formats"])

    def color(self, value: str) -> str:
        """Resolve a palette key or a literal hex color from the brand palette."""
        palette = self.data.get("palette", {})
        resolved = palette.get(value, value)
        if not isinstance(resolved, str) or not resolved.startswith("#"):
            raise ValidationError(f"رنگ «{value}» در پالت برند تعریف نشده است.")
        return resolved
