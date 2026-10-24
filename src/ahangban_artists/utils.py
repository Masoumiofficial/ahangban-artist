from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ValidationError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_text(path: str | Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    atomic_write_text(path, text)


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"فایل JSON پیدا نشد: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"JSON معتبر نیست ({path}): {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"ریشهٔ JSON باید object باشد: {path}")
    return value


def validate_display_name(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} الزامی است.")
    if value != value.strip():
        raise ValidationError(f"{label} نباید فاصلهٔ ابتدایی یا انتهایی داشته باشد.")
    if len(value) > 200 or any(unicodedata.category(char) == "Cc" for char in value):
        raise ValidationError(f"{label} شامل نویسهٔ کنترلی یا طول غیرمجاز است.")
    return value


def slugify_english(name: str) -> str:
    validate_display_name(name, "نام انگلیسی")
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        raise ValidationError("از نام انگلیسی نمی‌توان slug لاتین ساخت؛ نام را با حروف لاتین وارد کنید.")
    if len(slug) > 100:
        raise ValidationError("slug هنرمند بیش از ۱۰۰ نویسه است.")
    return slug


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def relative_path(path: str | Path, root: str | Path) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def append_text(path: str | Path, text: str) -> None:
    destination = Path(path)
    existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
    atomic_write_text(destination, existing + text)
