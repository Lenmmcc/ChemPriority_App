"""Short-lived, non-pickle persistence for page-6 auto-query checkpoints."""

from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import shutil
from typing import Any, Mapping
from uuid import uuid4

import pandas as pd

from src.auto_query_workflow import (
    AutoWorkflowChart,
    AutoWorkflowCheckpoint,
    AutoWorkflowMapping,
    AutoWorkflowModuleWorkbook,
    AutoWorkflowResult,
)


SCHEMA_VERSION = 1
TTL = timedelta(hours=24)
DEFAULT_CHECKPOINT_ROOT = Path(".cache/auto_query_runs")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class CheckpointStorageError(RuntimeError):
    """Base exception for checkpoint persistence failures."""


class InvalidRunToken(CheckpointStorageError):
    """Raised before filesystem access when a recovery token is malformed."""


class ExpiredCheckpoint(CheckpointStorageError):
    """Raised when a checkpoint has exceeded its 24-hour lifetime."""


@dataclass(frozen=True)
class LoadedAutoQueryCheckpoint:
    checkpoint: AutoWorkflowCheckpoint
    input_filename: str
    module_workbooks: OrderedDict[str, AutoWorkflowModuleWorkbook]
    manifest: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_run_token() -> str:
    return secrets.token_urlsafe(32)


def _run_directory(token, root=DEFAULT_CHECKPOINT_ROOT) -> Path:
    token = str(token)
    if not TOKEN_PATTERN.fullmatch(token):
        raise InvalidRunToken("恢复令牌格式无效")
    root = Path(root).resolve()
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    candidate = root / digest
    resolved = candidate.resolve()
    if (
        candidate.parent != root
        or candidate.name != digest
        or resolved != candidate
        or resolved.parent != root
    ):
        raise InvalidRunToken("恢复路径越界")
    return candidate


def _safe_name(value) -> str:
    value = str(value)
    if not NAME_PATTERN.fullmatch(value):
        raise CheckpointStorageError(f"不安全的检查点名称：{value}")
    return value


def _safe_file_name(value) -> str:
    value = str(value)
    if value in {".", ".."} or not FILE_NAME_PATTERN.fullmatch(value):
        raise CheckpointStorageError(f"不安全的文件名：{value}")
    return value


def _input_basename(value) -> str:
    value = str(value)
    basename = PureWindowsPath(PurePosixPath(value).name).name
    if basename in {"", ".", ".."}:
        raise CheckpointStorageError("输入文件名无效")
    return basename


def _validated_run_path(run_dir: Path, path: Path) -> Path:
    run_dir = run_dir.resolve()
    try:
        resolved = Path(path).resolve()
    except Exception as exc:
        raise CheckpointStorageError(f"无法解析检查点路径：{exc}") from exc
    if run_dir not in resolved.parents:
        raise CheckpointStorageError("检查点路径越界")
    return resolved


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_checkpoint_write(run_dir: Path, path: Path, payload: bytes) -> None:
    path = _validated_run_path(run_dir, path)
    _atomic_write(path, payload)


def _frame_bytes(frame: pd.DataFrame) -> bytes:
    text = frame.to_json(
        orient="table", date_format="iso", index=False, force_ascii=False
    )
    return gzip.compress(text.encode("utf-8"), mtime=0)


def _content_artifact_path(folder: str, payload: bytes, suffix: str) -> Path:
    digest = hashlib.sha256(payload).hexdigest()
    return Path(folder) / f"{digest}{suffix}"


def _write_immutable_artifact(
    run_dir: Path,
    relative: Path,
    payload: bytes,
) -> None:
    path = _validated_run_path(run_dir, run_dir / relative)
    if path.is_file():
        return
    _atomic_checkpoint_write(run_dir, path, payload)


def _read_frame(path: Path) -> pd.DataFrame:
    try:
        text = gzip.decompress(path.read_bytes()).decode("utf-8")
        return pd.read_json(io.StringIO(text), orient="table")
    except Exception as exc:
        raise CheckpointStorageError(
            f"无法读取检查点表格 {path.name}：{exc}"
        ) from exc


def save_checkpoint(
    token,
    checkpoint: AutoWorkflowCheckpoint,
    input_filename,
    module_workbooks: Mapping[str, AutoWorkflowModuleWorkbook],
    *,
    root=DEFAULT_CHECKPOINT_ROOT,
    now=None,
) -> Path:
    now = now or _utc_now()
    run_dir = _run_directory(token, root)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = _validated_run_path(run_dir, run_dir / "manifest.json")
    frames = OrderedDict(
        [
            ("representative_table", checkpoint.result.representative_table),
            ("step_status", checkpoint.result.step_status),
            ("warnings", checkpoint.result.warnings),
            *checkpoint.result.tables.items(),
        ]
    )
    table_files = {}
    for name, frame in frames.items():
        _safe_name(name)
        payload = _frame_bytes(frame)
        relative = _content_artifact_path("tables", payload, ".json.gz")
        _write_immutable_artifact(run_dir, relative, payload)
        table_files[name] = relative.as_posix()

    chart_files = {}
    for key, chart in checkpoint.result.charts.items():
        _safe_name(key)
        png = _content_artifact_path("charts", chart.png, ".png")
        pdf = _content_artifact_path("charts", chart.pdf, ".pdf")
        _write_immutable_artifact(run_dir, png, chart.png)
        _write_immutable_artifact(run_dir, pdf, chart.pdf)
        chart_files[key] = {
            "title": chart.title,
            "png": png.as_posix(),
            "pdf": pdf.as_posix(),
        }

    module_files = {}
    for slug, module in module_workbooks.items():
        _safe_name(slug)
        safe_file_name = _safe_file_name(module.file_name)
        relative = _content_artifact_path("modules", module.data, ".xlsx")
        _write_immutable_artifact(run_dir, relative, module.data)
        module_files[slug] = {
            "step": module.step,
            "file_name": safe_file_name,
            "path": relative.as_posix(),
        }

    created_at = now.isoformat()
    if manifest_path.exists():
        try:
            created_at = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )["created_at"]
        except Exception:
            created_at = now.isoformat()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "token_hash": run_dir.name,
        "run_id": checkpoint.run_id,
        "input_filename": _input_basename(input_filename),
        "input_signature": checkpoint.input_signature,
        "settings_signature": checkpoint.settings_signature,
        "selected_steps": list(checkpoint.selected_steps),
        "finished_steps": list(checkpoint.finished_steps),
        "current_step": checkpoint.current_step,
        "status": checkpoint.status,
        "error_message": checkpoint.error_message,
        "mapping": asdict(checkpoint.result.mapping),
        "table_files": table_files,
        "chart_files": chart_files,
        "module_files": module_files,
        "run_log": checkpoint.result.step_status.to_dict("records"),
        "warning_summary": checkpoint.result.warnings.to_dict("records"),
        "created_at": created_at,
        "updated_at": now.isoformat(),
        "expires_at": (now + TTL).isoformat(),
    }
    _atomic_checkpoint_write(
        run_dir,
        manifest_path,
        json.dumps(
            manifest, ensure_ascii=False, indent=2
        ).encode("utf-8"),
    )
    return run_dir


def _checked_relative_file(run_dir: Path, relative) -> Path:
    path = _validated_run_path(run_dir, run_dir / str(relative))
    if not path.is_file():
        raise CheckpointStorageError(f"检查点文件缺失：{relative}")
    return path


def _read_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise TypeError("manifest root must be an object")
        return manifest
    except Exception as exc:
        raise CheckpointStorageError(f"检查点清单损坏：{exc}") from exc


def _manifest_expiry(manifest: Mapping[str, Any]) -> datetime:
    try:
        expires_at = datetime.fromisoformat(str(manifest["expires_at"]))
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return expires_at
    except Exception as exc:
        raise CheckpointStorageError(f"检查点过期时间无效：{exc}") from exc


def load_checkpoint(token, *, root=DEFAULT_CHECKPOINT_ROOT, now=None):
    now = now or _utc_now()
    run_dir = _run_directory(token, root)
    manifest_path = _validated_run_path(run_dir, run_dir / "manifest.json")
    if not manifest_path.is_file():
        raise CheckpointStorageError("找不到可恢复的检查点")
    manifest = _read_manifest(manifest_path)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("token_hash") != run_dir.name
    ):
        raise CheckpointStorageError("检查点版本或令牌摘要不匹配")
    if now > _manifest_expiry(manifest):
        raise ExpiredCheckpoint("检查点已超过 24 小时")

    try:
        frames = OrderedDict(
            (
                _safe_name(name),
                _read_frame(_checked_relative_file(run_dir, relative)),
            )
            for name, relative in manifest["table_files"].items()
        )
        charts = OrderedDict()
        for key, entry in manifest["chart_files"].items():
            safe_key = _safe_name(key)
            charts[safe_key] = AutoWorkflowChart(
                title=entry["title"],
                png=_checked_relative_file(run_dir, entry["png"]).read_bytes(),
                pdf=_checked_relative_file(run_dir, entry["pdf"]).read_bytes(),
            )
        modules = OrderedDict()
        for slug, entry in manifest["module_files"].items():
            safe_slug = _safe_name(slug)
            modules[safe_slug] = AutoWorkflowModuleWorkbook(
                step=entry["step"],
                slug=safe_slug,
                file_name=_safe_file_name(entry["file_name"]),
                data=_checked_relative_file(run_dir, entry["path"]).read_bytes(),
            )

        result = AutoWorkflowResult(
            mapping=AutoWorkflowMapping(**manifest["mapping"]),
            representative_table=frames.pop("representative_table"),
            tables=OrderedDict(
                (name, frame)
                for name, frame in frames.items()
                if name not in {"step_status", "warnings"}
            ),
            step_status=frames["step_status"],
            warnings=frames["warnings"],
            charts=charts,
        )
        checkpoint = AutoWorkflowCheckpoint(
            run_id=manifest["run_id"],
            input_signature=manifest["input_signature"],
            settings_signature=manifest["settings_signature"],
            selected_steps=tuple(manifest["selected_steps"]),
            finished_steps=tuple(manifest["finished_steps"]),
            current_step=manifest["current_step"],
            status=manifest["status"],
            result=result,
            error_message=manifest["error_message"],
            updated_at=manifest["updated_at"],
        )
        return LoadedAutoQueryCheckpoint(
            checkpoint, manifest["input_filename"], modules, manifest
        )
    except CheckpointStorageError:
        raise
    except Exception as exc:
        raise CheckpointStorageError(f"检查点清单内容无效：{exc}") from exc


def delete_checkpoint(token, *, root=DEFAULT_CHECKPOINT_ROOT) -> bool:
    run_dir = _run_directory(token, root)
    if not run_dir.exists():
        return False
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise CheckpointStorageError("检查点摘要目录无效")
    shutil.rmtree(run_dir)
    return True


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _latest_trusted_mtime(run_dir: Path) -> datetime:
    latest = run_dir.stat().st_mtime
    pending = [run_dir]
    while pending:
        directory = pending.pop()
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        for child in children:
            if _is_link_or_junction(child):
                continue
            try:
                resolved = _validated_run_path(run_dir, child)
                stat_result = resolved.stat()
            except (CheckpointStorageError, OSError):
                continue
            latest = max(latest, stat_result.st_mtime)
            if resolved.is_dir():
                pending.append(resolved)
    return datetime.fromtimestamp(latest, tz=timezone.utc)


def cleanup_expired_checkpoints(*, root=DEFAULT_CHECKPOINT_ROOT, now=None):
    now = now or _utc_now()
    root = Path(root).resolve()
    if not root.exists():
        return []
    removed = []
    for child in root.iterdir():
        if (
            child.is_symlink()
            or not child.is_dir()
            or not HASH_PATTERN.fullmatch(child.name)
            or child.resolve() != child
            or child.parent != root
        ):
            continue
        try:
            manifest_path = _validated_run_path(child, child / "manifest.json")
            manifest = _read_manifest(manifest_path)
            if (
                manifest.get("schema_version") != SCHEMA_VERSION
                or manifest.get("token_hash") != child.name
            ):
                raise CheckpointStorageError("检查点版本或令牌摘要不匹配")
            expired = now > _manifest_expiry(manifest)
        except CheckpointStorageError:
            try:
                expired = now > _latest_trusted_mtime(child) + TTL
            except OSError:
                expired = False
        if expired:
            shutil.rmtree(child)
            removed.append(child)
    return removed
