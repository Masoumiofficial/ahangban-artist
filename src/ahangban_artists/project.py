from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ExistingArtistError, QualityGateError, ValidationError
from .images import (
    analyze_reference,
    circular_preview,
    compose_final,
    load_png,
    png_bytes,
    prepare_master,
)
from .settings import Settings
from .utils import (
    append_text,
    atomic_write_json,
    atomic_write_text,
    normalized_name,
    read_json,
    relative_path,
    sha256_bytes,
    sha256_file,
    slugify_english,
    utc_now,
    validate_display_name,
)


SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MASTER_NAME = "artist-master.png"
FINAL_NAME = "artist-1080x1080.png"
VERIFIED_PREVIEW_NAME = "artist-verified-preview.png"
UNVERIFIED_PREVIEW_NAME = "artist-unverified-preview.png"


def preview_name(verified: bool) -> str:
    return VERIFIED_PREVIEW_NAME if verified else UNVERIFIED_PREVIEW_NAME


@dataclass
class ArtistProject:
    settings: Settings
    artist_dir: Path
    metadata: dict[str, Any]

    @classmethod
    def load(cls, settings: Settings, slug: str) -> "ArtistProject":
        if not SLUG_PATTERN.fullmatch(slug):
            raise ValidationError("slug هنرمند معتبر نیست.")
        artist_dir = settings.artists_root / slug
        metadata_path = artist_dir / "metadata" / "artist.json"
        metadata = read_json(metadata_path)
        if metadata.get("slug") != slug:
            raise ValidationError("slug مسیر و فرادادهٔ هنرمند یکسان نیست.")
        return cls(settings, artist_dir, metadata)

    @property
    def slug(self) -> str:
        return str(self.metadata["slug"])

    @property
    def metadata_path(self) -> Path:
        return self.artist_dir / "metadata" / "artist.json"

    @property
    def qc_path(self) -> Path:
        return self.artist_dir / "metadata" / "qc.json"

    @property
    def changelog_path(self) -> Path:
        return self.artist_dir / "history" / "CHANGELOG.md"

    @property
    def master_path(self) -> Path:
        return self.artist_dir / "portrait" / MASTER_NAME

    @property
    def final_path(self) -> Path:
        return self.artist_dir / "portrait" / FINAL_NAME

    @property
    def reference_path(self) -> Path:
        return self.settings.path(self.metadata["paths"]["reference"])

    @property
    def verified(self) -> bool:
        return bool(self.metadata["verification"]["verified"])

    def preview_path(self, verified: bool | None = None) -> Path:
        state = self.verified if verified is None else verified
        return self.artist_dir / "portrait" / preview_name(state)

    def save_metadata(self) -> None:
        atomic_write_json(self.metadata_path, self.metadata)

    def assert_immutable_inputs_intact(self) -> None:
        expected_reference = self.metadata["reference"]["sha256"]
        if not self.reference_path.is_file() or sha256_file(self.reference_path) != expected_reference:
            raise ValidationError("تصویر مرجع اصلی تغییر کرده یا حذف شده است؛ عملیات متوقف شد.")

        assets = self.metadata.get("assets", {})
        expected_logo = assets.get("logo_sha256")
        if expected_logo and sha256_file(self.settings.logo_path) != expected_logo:
            raise ValidationError("هش لوگوی رسمی با نسخهٔ ثبت‌شدهٔ پروژه ناسازگار است.")
        for layer_name, expected_hash in assets.get("badge_sha256", {}).items():
            layer_path = self.artist_dir / "badges" / layer_name
            if not layer_path.is_file() or sha256_file(layer_path) != expected_hash:
                raise ValidationError(f"لایهٔ نشان «{layer_name}» تغییر کرده یا حذف شده است.")

    def refresh_qc(self) -> dict[str, Any]:
        from .qc import run_qc

        report = run_qc(self.settings, self.artist_dir, self.metadata)
        atomic_write_json(self.qc_path, report)
        self.metadata["quality"] = {
            "technical_status": report["technical_status"],
            "acceptance_status": report["acceptance_status"],
            "last_checked_at": report["checked_at"],
            "report": relative_path(self.qc_path, self.settings.root),
        }
        self.save_metadata()
        return report


def _check_duplicate_names(
    settings: Settings,
    name_fa: str,
    name_en: str,
    allow_duplicate_names: bool,
) -> list[str]:
    duplicates: list[str] = []
    for metadata_path in sorted(settings.artists_root.glob("*/metadata/artist.json")):
        metadata = read_json(metadata_path)
        if normalized_name(str(metadata.get("name_fa", ""))) == normalized_name(name_fa) or normalized_name(
            str(metadata.get("name_en", ""))
        ) == normalized_name(name_en):
            duplicates.append(str(metadata.get("slug", metadata_path.parent.parent.name)))
    if duplicates and not allow_duplicate_names:
        raise ExistingArtistError(
            "احتمال تکرار هنرمند؛ نام در این پوشه‌ها موجود است: "
            + ", ".join(duplicates)
            + ". ابتدا فراداده را بررسی کنید؛ فقط اگر هنرمند متفاوت است، --allow-duplicate-names را صریح بدهید."
        )
    return duplicates


def intake(
    settings: Settings,
    reference: str | Path,
    name_fa: str,
    name_en: str,
    verified: bool = False,
    verification_explicit: bool = False,
    notes: str = "",
    allow_duplicate_names: bool = False,
) -> ArtistProject:
    name_fa = validate_display_name(name_fa, "نام فارسی")
    name_en = validate_display_name(name_en, "نام انگلیسی")
    slug = slugify_english(name_en)
    artist_dir = settings.artists_root / slug
    if artist_dir.exists():
        raise ExistingArtistError(
            f"پوشهٔ هنرمند از قبل موجود است: {artist_dir}. مرجع یا خروجی موجود بازنویسی نشد؛ وضعیت را بررسی کنید."
        )

    reference_info = analyze_reference(reference, settings)
    settings.artists_root.mkdir(parents=True, exist_ok=True)
    duplicate_slugs = _check_duplicate_names(settings, name_fa, name_en, allow_duplicate_names)
    now = utc_now()
    verification_status = "verified" if verified else "unverified"
    verification_source = "user-explicit" if verification_explicit else "default-unverified"

    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{slug}.intake-", dir=settings.artists_root))
    try:
        for directory in ("source", "portrait", "badges", "metadata", "history"):
            (temporary_dir / directory).mkdir()

        reference_name = f"reference-original{reference_info['suffix']}"
        temporary_reference = temporary_dir / "source" / reference_name
        shutil.copyfile(reference, temporary_reference)
        if sha256_file(temporary_reference) != reference_info["sha256"]:
            raise ValidationError("هش نسخهٔ ذخیره‌شدهٔ مرجع با فایل اصلی یکسان نیست.")

        badge_hashes: dict[str, str] = {}
        for layer_name in settings.badge_layer_names:
            source_badge = settings.canonical_badges_path / layer_name
            target_badge = temporary_dir / "badges" / layer_name
            shutil.copyfile(source_badge, target_badge)
            badge_hashes[layer_name] = sha256_file(target_badge)

        reference_relative = f"brand-kit/artists/{slug}/source/{reference_name}"
        metadata: dict[str, Any] = {
            "schema_version": "1.0",
            "artist_id": slug,
            "name_fa": name_fa,
            "name_en": name_en,
            "slug": slug,
            "verification": {
                "status": verification_status,
                "verified": verified,
                "source": verification_source,
                "updated_at": now,
                "note": "Verification is project data; a badge on an image is not identity evidence.",
            },
            "created_at": now,
            "updated_at": now,
            "design_version": settings.design_version,
            "revision": 0,
            "paths": {
                "reference": reference_relative,
                "master": f"brand-kit/artists/{slug}/portrait/{MASTER_NAME}",
                "final": None,
                "preview": None,
                "prompt": relative_path(settings.prompt_path, settings.root),
                "logo": relative_path(settings.logo_path, settings.root),
                "badges": [
                    f"brand-kit/artists/{slug}/badges/{layer_name}"
                    for layer_name in settings.badge_layer_names
                ],
                "changelog": f"brand-kit/artists/{slug}/history/CHANGELOG.md",
            },
            "reference": reference_info,
            "assets": {
                "logo_sha256": sha256_file(settings.logo_path),
                "badge_sha256": badge_hashes,
                "logo_origin": settings.data["source_of_truth"],
            },
            "generation": {
                "status": "awaiting-external-master",
                "prompt_version": settings.prompt_version,
                "mode": "reference-preserving external edit required",
                "identity_guaranteed_by_pipeline": False,
                "generator": None,
            },
            "review": {
                "status": "pending",
                "required_checks": [
                    "identity-preserved",
                    "face-safe-in-central-80-percent-and-circle",
                    "readable-at-small-size",
                    "no-unintended-text-or-watermark",
                    "consistent-editorial-brand-direction",
                ],
            },
            "changes": [
                {
                    "revision": 0,
                    "at": now,
                    "type": "intake",
                    "description": notes.strip() or "پذیرش مرجع و ایجاد پروژهٔ مستقل هنرمند.",
                }
            ],
        }
        if duplicate_slugs:
            metadata["intake_duplicate_name_override"] = {
                "matching_slugs": duplicate_slugs,
                "approved_explicitly": True,
            }

        atomic_write_json(temporary_dir / "metadata" / "artist.json", metadata)
        changelog = (
            f"# تاریخچهٔ کاور {name_fa} / {name_en}\n\n"
            f"- شناسه: `{slug}`\n"
            f"- وضعیت تأیید در پذیرش: **{verification_status}** ({verification_source})\n"
            f"- نسخهٔ طراحی: `{settings.design_version}`\n\n"
            f"## پذیرش — {now}\n\n"
            f"- {metadata['changes'][0]['description']}\n"
            f"- مرجع با SHA-256 `{reference_info['sha256']}` و بدون تغییر بایت‌ها ذخیره شد.\n"
            "- پرتره هنوز تولید نشده است؛ پرامپت ثابت و ساختار پروژه آماده است.\n\n"
        )
        atomic_write_text(temporary_dir / "history" / "CHANGELOG.md", changelog)
        temporary_dir.rename(artist_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise

    return ArtistProject(settings, artist_dir, metadata)


def _known_portrait_paths(project: ArtistProject) -> list[Path]:
    return [
        project.master_path,
        project.final_path,
        project.preview_path(True),
        project.preview_path(False),
    ]


def _archive_current(project: ArtistProject, include_master: bool, reason: str) -> str:
    revisions_root = project.artist_dir / "history" / "revisions"
    revisions_root.mkdir(parents=True, exist_ok=True)
    existing_numbers = [
        int(match.group(1))
        for path in revisions_root.iterdir()
        if (match := re.fullmatch(r"v(\d{3})", path.name))
    ]
    archive = revisions_root / f"v{max(existing_numbers, default=0) + 1:03d}"
    (archive / "portrait").mkdir(parents=True)
    (archive / "metadata").mkdir()

    files: list[dict[str, str]] = []
    for path in _known_portrait_paths(project):
        if path == project.master_path and not include_master:
            continue
        if path.is_file():
            destination = archive / "portrait" / path.name
            shutil.copy2(path, destination)
            files.append({"path": relative_path(path, project.settings.root), "sha256": sha256_file(path)})

    for path in (project.metadata_path, project.qc_path):
        if path.is_file():
            destination = archive / "metadata" / path.name
            shutil.copy2(path, destination)
            files.append({"path": relative_path(path, project.settings.root), "sha256": sha256_file(path)})

    manifest = {
        "archived_at": utc_now(),
        "artist_id": project.metadata["artist_id"],
        "source_revision": project.metadata.get("revision", 0),
        "reason": reason,
        "master_included": include_master,
        "files": files,
    }
    atomic_write_json(archive / "manifest.json", manifest)
    return relative_path(archive, project.settings.root)


def _record_final_metadata(
    project: ArtistProject,
    now: str,
    description: str,
    change_type: str,
    archive_path: str | None,
) -> None:
    project.metadata["revision"] = int(project.metadata.get("revision", 0)) + 1
    project.metadata["updated_at"] = now
    project.metadata["paths"]["master"] = relative_path(project.master_path, project.settings.root)
    project.metadata["paths"]["final"] = relative_path(project.final_path, project.settings.root)
    project.metadata["paths"]["preview"] = relative_path(project.preview_path(), project.settings.root)
    project.metadata["generation"]["status"] = "finalized-pending-visual-review"
    project.metadata["review"] = {
        "status": "pending",
        "required_checks": [
            "identity-preserved",
            "face-safe-in-central-80-percent-and-circle",
            "readable-at-small-size",
            "no-unintended-text-or-watermark",
            "consistent-editorial-brand-direction",
        ],
    }
    change: dict[str, Any] = {
        "revision": project.metadata["revision"],
        "at": now,
        "type": change_type,
        "description": description,
        "verification": project.metadata["verification"]["status"],
        "master_sha256": sha256_file(project.master_path),
        "final_sha256": sha256_file(project.final_path),
    }
    if archive_path:
        change["previous_version_archive"] = archive_path
    project.metadata.setdefault("changes", []).append(change)


def finalize(
    settings: Settings,
    slug: str,
    master: str | Path,
    change: str = "",
    on_existing: str = "keep",
    generator: str | None = None,
) -> tuple[ArtistProject, dict[str, Any]]:
    project = ArtistProject.load(settings, slug)
    project.assert_immutable_inputs_intact()
    if on_existing not in {"keep", "archive"}:
        raise ValidationError("on_existing باید keep یا archive باشد.")

    existing = any(path.exists() for path in _known_portrait_paths(project))
    if existing and on_existing == "keep":
        raise ExistingArtistError(
            "پرتره یا خروجی نهایی قبلی موجود است. برای حفظ وضعیت فعلی هیچ تغییری ندهید؛ "
            "برای جایگزینی همراه با بایگانی، --on-existing archive و شرح تغییر را صریح بدهید."
        )
    description = change.strip() or "نهایی‌سازی نخستین پرترهٔ بیرونی و ساخت خروجی برند."
    if existing and not change.strip():
        raise ValidationError("برای جایگزینی نسخهٔ قبلی، شرح تغییر با --change الزامی است.")

    prepared_master, transform = prepare_master(master, settings)
    final = compose_final(prepared_master, settings, project.artist_dir, project.verified)
    preview = circular_preview(final)
    master_bytes = png_bytes(prepared_master)
    final_bytes = png_bytes(final)
    preview_bytes = png_bytes(preview)

    archive_path = _archive_current(project, include_master=True, reason=description) if existing else None
    from .utils import atomic_write_bytes

    atomic_write_bytes(project.master_path, master_bytes)
    atomic_write_bytes(project.final_path, final_bytes)
    atomic_write_bytes(project.preview_path(), preview_bytes)
    other_preview = project.preview_path(not project.verified)
    if other_preview.exists():
        other_preview.unlink()

    now = utc_now()
    project.metadata["generation"].update(
        {
            "generator": generator.strip() if generator and generator.strip() else "unspecified-external-master",
            "external_master_sha256": sha256_file(master),
            "transform": transform,
            "master_sha256": sha256_bytes(master_bytes),
        }
    )
    _record_final_metadata(project, now, description, "finalization", archive_path)
    project.save_metadata()
    report = project.refresh_qc()

    archive_line = f"- بایگانی نسخهٔ قبلی: `{archive_path}`\n" if archive_path else ""
    append_text(
        project.changelog_path,
        f"## بازبینی {project.metadata['revision']} — {now}\n\n"
        f"- {description}\n"
        f"- وضعیت تأیید: **{project.metadata['verification']['status']}**\n"
        f"- پرترهٔ اصلی: `{relative_path(project.master_path, settings.root)}`\n"
        f"- خروجی نهایی: `{relative_path(project.final_path, settings.root)}`\n"
        f"- SHA-256 خروجی: `{sha256_file(project.final_path)}`\n"
        f"{archive_line}"
        f"- QC فنی: **{report['technical_status']}**؛ پذیرش: **{report['acceptance_status']}**\n\n",
    )
    return project, report


def set_verification(
    settings: Settings,
    slug: str,
    verified: bool,
    reason: str = "",
) -> tuple[ArtistProject, dict[str, Any], bool]:
    project = ArtistProject.load(settings, slug)
    project.assert_immutable_inputs_intact()
    if project.verified == verified:
        report = project.refresh_qc()
        return project, report, False
    if not project.master_path.is_file() or not project.final_path.is_file():
        raise ValidationError("برای تغییر وضعیت، ابتدا پرترهٔ اصلی و خروجی نهایی را بسازید.")

    description = reason.strip() or (
        "تغییر صریح وضعیت هنرمند به تأییدشده و بازسازی لایهٔ نشان."
        if verified
        else "تغییر صریح وضعیت هنرمند به تأییدنشده و حذف لایه‌های نشان از خروجی."
    )
    master_image = load_png(project.master_path)
    final = compose_final(master_image, settings, project.artist_dir, verified)
    preview = circular_preview(final)
    final_bytes = png_bytes(final)
    preview_bytes = png_bytes(preview)
    archive_path = _archive_current(project, include_master=False, reason=description)

    from .utils import atomic_write_bytes

    atomic_write_bytes(project.final_path, final_bytes)
    atomic_write_bytes(project.preview_path(verified), preview_bytes)
    old_preview = project.preview_path(not verified)
    if old_preview.exists():
        old_preview.unlink()

    now = utc_now()
    project.metadata["verification"] = {
        "status": "verified" if verified else "unverified",
        "verified": verified,
        "source": "user-explicit",
        "updated_at": now,
        "note": "Verification is project data; a badge on an image is not identity evidence.",
    }
    _record_final_metadata(project, now, description, "verification-change", archive_path)
    project.save_metadata()
    report = project.refresh_qc()
    append_text(
        project.changelog_path,
        f"## بازبینی {project.metadata['revision']} — تغییر وضعیت تأیید — {now}\n\n"
        f"- {description}\n"
        f"- وضعیت جدید: **{project.metadata['verification']['status']}** (user-explicit)\n"
        f"- پرترهٔ اصلی بدون تغییر: `{sha256_file(project.master_path)}`\n"
        f"- بایگانی نسخهٔ قبلی: `{archive_path}`\n"
        f"- QC فنی: **{report['technical_status']}**؛ پذیرش: **{report['acceptance_status']}**\n\n",
    )
    return project, report, True


def approve_visual_review(
    settings: Settings,
    slug: str,
    reviewer: str,
    notes: str = "",
) -> tuple[ArtistProject, dict[str, Any]]:
    project = ArtistProject.load(settings, slug)
    project.assert_immutable_inputs_intact()
    report = project.refresh_qc()
    if report["technical_status"] != "pass":
        raise QualityGateError("QC فنی شکست خورده است؛ تأیید بصری نمی‌تواند آن را نادیده بگیرد.")
    reviewer = validate_display_name(reviewer, "نام بازبین")
    now = utc_now()
    project.metadata["updated_at"] = now
    project.metadata["review"] = {
        "status": "approved",
        "revision": project.metadata["revision"],
        "reviewer": reviewer,
        "reviewed_at": now,
        "notes": notes.strip(),
        "master_sha256": sha256_file(project.master_path),
        "final_sha256": sha256_file(project.final_path),
        "checks": {
            "identity_preserved": True,
            "face_safe_in_central_80_percent_and_circle": True,
            "readable_at_small_size": True,
            "no_unintended_text_or_watermark": True,
            "consistent_editorial_brand_direction": True,
        },
    }
    project.save_metadata()
    report = project.refresh_qc()
    if report["acceptance_status"] == "pass":
        project.metadata["generation"]["status"] = "accepted"
        project.save_metadata()
    review_note = f"- {notes.strip()}\n" if notes.strip() else ""
    append_text(
        project.changelog_path,
        f"## تأیید بازبینی بصری — {now}\n\n"
        f"- بازبین: {reviewer}\n"
        f"- بازبینی {project.metadata['revision']} پس از مشاهدهٔ مرجع، نسخهٔ اصلی، خروجی، اندازهٔ کوچک و برش دایره‌ای تأیید شد.\n"
        + review_note,
    )
    return project, report
