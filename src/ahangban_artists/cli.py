from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .errors import ArtistCoverError
from .project import (
    ArtistProject,
    approve_visual_review,
    finalize,
    intake,
    set_verification,
)
from .settings import Settings
from .utils import read_json, relative_path


def _add_verification_flags(parser: argparse.ArgumentParser, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--verified", dest="verified", action="store_true", help="وضعیت صریح تأییدشده")
    group.add_argument("--unverified", dest="verified", action="store_false", help="وضعیت صریح تأییدنشده")
    parser.set_defaults(verified=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ahangban-artist",
        description="سیستم قابل‌تکرار مدیریت و کنترل کیفیت کاور هنرمندان آهنگ‌بان",
    )
    parser.add_argument("--root", default=".", help="ریشهٔ مخزن؛ پیش‌فرض: پوشهٔ جاری")
    commands = parser.add_subparsers(dest="command", required=True)

    intake_parser = commands.add_parser("intake", help="پذیرش مرجع و ساخت پروژه؛ بدون ادعای تولید پرتره")
    intake_parser.add_argument("--reference", required=True, help="تصویر مرجع هنرمند")
    intake_parser.add_argument("--name-fa", required=True, help="نام فارسی، بدون تغییر معنایی")
    intake_parser.add_argument("--name-en", required=True, help="نام انگلیسی/لاتین برای slug")
    _add_verification_flags(intake_parser, required=False)
    intake_parser.add_argument("--notes", default="", help="توضیح نسخه یا پذیرش")
    intake_parser.add_argument(
        "--allow-duplicate-names",
        action="store_true",
        help="تأیید صریح اینکه نام یکسان متعلق به هنرمند متفاوت است",
    )

    finalize_parser = commands.add_parser("finalize", help="نهایی‌سازی پرترهٔ تولیدشده در مدل بیرونی")
    finalize_parser.add_argument("--artist", required=True, help="slug هنرمند")
    finalize_parser.add_argument("--master", required=True, help="پرترهٔ ویرایش‌شدهٔ بیرونی، بدون لوگو و نشان")
    finalize_parser.add_argument("--change", default="", help="شرح تغییر؛ برای نسخهٔ جایگزین اجباری است")
    finalize_parser.add_argument(
        "--on-existing",
        choices=("keep", "archive"),
        default="keep",
        help="keep: توقف بدون بازنویسی؛ archive: بایگانی نسخهٔ قبلی و جایگزینی",
    )
    finalize_parser.add_argument("--generator", default=None, help="نام واقعی ابزار/مدل بیرونی، در صورت اطلاع")

    status_parser = commands.add_parser("set-verification", help="تغییر دادهٔ تأیید و بازسازی خروجی بدون تغییر master")
    status_parser.add_argument("--artist", required=True)
    _add_verification_flags(status_parser, required=True)
    status_parser.add_argument("--reason", default="", help="شرح تغییر وضعیت")

    qc_parser = commands.add_parser("qc", help="اجرای کنترل‌های فنی و ثبت metadata/qc.json")
    qc_parser.add_argument("--artist", required=True)

    approve_parser = commands.add_parser("approve", help="ثبت تأیید بصری پس از مشاهدهٔ واقعی خروجی")
    approve_parser.add_argument("--artist", required=True)
    approve_parser.add_argument("--reviewer", required=True, help="نام بازبین انسانی یا ایجنت دارای مشاهدهٔ تصویر")
    approve_parser.add_argument("--notes", default="")

    report_parser = commands.add_parser("report", help="گزارش وضعیت واقعی پروژه و مسیرها")
    report_parser.add_argument("--artist", required=True)
    report_parser.add_argument("--json", action="store_true", help="خروجی JSON")

    return parser


def _project_report(project: ArtistProject) -> dict[str, Any]:
    qc_path = project.qc_path
    qc = read_json(qc_path) if qc_path.is_file() else None
    final_info = qc.get("files", {}).get("final") if qc else None
    limitations: list[str] = []
    if project.metadata.get("generation", {}).get("status") == "awaiting-external-master":
        limitations.append(
            "پرتره تولید نشده است؛ این مخزن مدل تصویرساز داخلی ندارد و به ویرایش مبتنی بر مرجع در ابزار بیرونی نیاز دارد."
        )
    if qc:
        limitations.extend(qc.get("limitations", []))
    else:
        limitations.append("QC هنوز اجرا نشده است؛ خروجی نهایی فعلاً وجود ندارد.")

    technical = None
    if final_info:
        technical = {
            "width": final_info["width"],
            "height": final_info["height"],
            "format": final_info["format"],
            "mode": final_info["mode"],
            "srgb_profile": final_info["srgb_profile"],
            "bytes": final_info["bytes"],
            "sha256": final_info["sha256"],
        }

    return {
        "name_fa": project.metadata["name_fa"],
        "name_en": project.metadata["name_en"],
        "artist_id": project.metadata["artist_id"],
        "verification": project.metadata["verification"],
        "artist_directory": relative_path(project.artist_dir, project.settings.root),
        "master_without_badge": (
            relative_path(project.master_path, project.settings.root) if project.master_path.is_file() else None
        ),
        "final_image": relative_path(project.final_path, project.settings.root) if project.final_path.is_file() else None,
        "circular_preview": project.metadata.get("paths", {}).get("preview"),
        "technical": technical,
        "revision": project.metadata.get("revision"),
        "changes": project.metadata.get("changes", []),
        "quality": {
            "technical_status": qc.get("technical_status") if qc else "not-run",
            "visual_review_status": qc.get("visual_review_status") if qc else "not-run",
            "acceptance_status": qc.get("acceptance_status") if qc else "not-run",
        },
        "remaining_limitations": limitations,
        "github_operations": "not-attempted-by-cli; verify real Git/GitHub state separately with git or gh",
    }


def _print_report(report: dict[str, Any]) -> None:
    print(f"هنرمند: {report['name_fa']} / {report['name_en']}")
    print(f"وضعیت تأیید: {report['verification']['status']} ({report['verification']['source']})")
    print(f"پوشه: {report['artist_directory']}")
    print(f"نسخهٔ اصلی بدون نشان: {report['master_without_badge'] or 'هنوز وجود ندارد'}")
    print(f"تصویر نهایی: {report['final_image'] or 'هنوز تولید نشده'}")
    print(f"پیش‌نمایش دایره‌ای: {report['circular_preview'] or 'هنوز تولید نشده'}")
    if report["technical"]:
        item = report["technical"]
        print(
            f"مشخصات فنی: {item['width']}×{item['height']}، {item['format']}، "
            f"{item['mode']}، sRGB={item['srgb_profile']}، {item['bytes']} بایت"
        )
    quality = report["quality"]
    print(
        f"کیفیت: فنی={quality['technical_status']}، بصری={quality['visual_review_status']}، "
        f"پذیرش={quality['acceptance_status']}"
    )
    print(f"بازبینی طراحی: {report['revision']}")
    for change in report["changes"]:
        print(f"- تغییر r{change.get('revision')}: {change.get('description')}")
    for limitation in report["remaining_limitations"]:
        print(f"محدودیت: {limitation}")
    print(f"عملیات GitHub این دستور: {report['github_operations']}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(Path(args.root))

        if args.command == "intake":
            explicit = args.verified is not None
            project = intake(
                settings,
                reference=args.reference,
                name_fa=args.name_fa,
                name_en=args.name_en,
                verified=bool(args.verified),
                verification_explicit=explicit,
                notes=args.notes,
                allow_duplicate_names=args.allow_duplicate_names,
            )
            print(f"پروژه ساخته شد: {relative_path(project.artist_dir, settings.root)}")
            print(f"نام: {project.metadata['name_fa']} / {project.metadata['name_en']}")
            print(f"وضعیت تأیید: {project.metadata['verification']['status']}")
            print("پرتره تولید نشد؛ مدل تصویرساز داخلی در دسترس نیست. مرجع، پرامپت، فراداده و لایه‌ها آماده‌اند.")
            for warning in project.metadata["reference"]["warnings"]:
                print(f"هشدار مرجع: {warning}")
            print(f"گام بعد: finalize --artist {project.slug} --master <edited-portrait>")
            return 0

        if args.command == "finalize":
            project, report = finalize(
                settings,
                slug=args.artist,
                master=args.master,
                change=args.change,
                on_existing=args.on_existing,
                generator=args.generator,
            )
            print(f"خروجی‌ها ساخته شدند: {relative_path(project.final_path, settings.root)}")
            print(f"QC فنی: {report['technical_status']}؛ پذیرش: {report['acceptance_status']}")
            if report["acceptance_status"] == "pending-visual-review":
                print("هویت، امنی چهره و خوانایی هنوز تأیید نشده‌اند؛ پس از مشاهدهٔ واقعی، دستور approve را اجرا کنید.")
            return 0 if report["technical_status"] == "pass" else 1

        if args.command == "set-verification":
            project, report, changed = set_verification(settings, args.artist, args.verified, args.reason)
            print(
                f"وضعیت: {project.metadata['verification']['status']}؛ "
                f"{'خروجی بازسازی شد' if changed else 'تغییری لازم نبود'}؛ QC فنی: {report['technical_status']}"
            )
            return 0 if report["technical_status"] == "pass" else 1

        if args.command == "qc":
            project = ArtistProject.load(settings, args.artist)
            report = project.refresh_qc()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["technical_status"] == "pass" else 1

        if args.command == "approve":
            project, report = approve_visual_review(settings, args.artist, args.reviewer, args.notes)
            print(f"بازبینی بصری ثبت شد؛ پذیرش: {report['acceptance_status']}")
            return 0 if report["acceptance_status"] == "pass" else 1

        if args.command == "report":
            project = ArtistProject.load(settings, args.artist)
            report = _project_report(project)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                _print_report(report)
            return 0

        parser.error("دستور ناشناخته")
        return 2
    except ArtistCoverError as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("عملیات لغو شد.", file=sys.stderr)
        return 130
