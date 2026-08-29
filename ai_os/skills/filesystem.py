"""File operations: list, find, move, rename, organize, delete."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ..trash import restore_from_trash, send_to_trash
from .base import (
    ActionDef,
    ActionResult,
    ExecContext,
    Param,
    RiskLevel,
    Skill,
    UndoRecord,
)

# Extension buckets used by "organize my desktop".
CATEGORIES: dict[str, tuple[str, ...]] = {
    "Images": (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".heic"),
    "Documents": (".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".pages"),
    "Spreadsheets": (".xls", ".xlsx", ".csv", ".ods"),
    "Presentations": (".ppt", ".pptx", ".odp", ".key"),
    "Audio": (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"),
    "Video": (".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"),
    "Archives": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"),
    "Installers": (".exe", ".msi", ".msix", ".appx"),
    "Code": (".py", ".js", ".ts", ".json", ".html", ".css", ".java", ".c", ".cpp", ".rs", ".go"),
}


def _expand(value: str) -> Path:
    return Path(value).expanduser()


def category_for(path: Path) -> str:
    suffix = path.suffix.lower()
    for name, extensions in CATEGORIES.items():
        if suffix in extensions:
            return name
    return "Other"


def _unique_target(target: Path) -> Path:
    """Never silently overwrite: append a counter instead."""
    if not target.exists():
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    counter = 2
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


class FileSystemSkill(Skill):
    name = "filesystem"
    description = "Inspect and reorganize files and folders."

    def actions(self) -> Sequence[ActionDef]:
        return (
            ActionDef(
                name="fs_list",
                skill=self.name,
                description="List the entries in a folder.",
                risk=RiskLevel.READ_ONLY,
                handler=self.list_folder,
                params=(
                    Param("path", description="Folder to list.", is_path=True),
                    Param("pattern", description="Optional glob filter, e.g. '*.pdf'.",
                          required=False, default="*"),
                ),
                summarize=lambda a: f"List {a['path']}",
            ),
            ActionDef(
                name="fs_find",
                skill=self.name,
                description="Search a folder tree for files matching a glob pattern.",
                risk=RiskLevel.READ_ONLY,
                handler=self.find,
                params=(
                    Param("path", description="Folder to search.", is_path=True),
                    Param("pattern", description="Glob pattern, e.g. '*.log'."),
                    Param("max_results", type="integer", required=False, default=200,
                          description="Maximum number of matches to return."),
                ),
                summarize=lambda a: f"Find {a['pattern']} under {a['path']}",
            ),
            ActionDef(
                name="fs_create_folder",
                skill=self.name,
                description="Create a new folder.",
                risk=RiskLevel.REVERSIBLE,
                handler=self.create_folder,
                reversible=True,
                params=(Param("path", description="Folder to create.", is_path=True),),
                summarize=lambda a: f"Create folder {a['path']}",
            ),
            ActionDef(
                name="fs_move",
                skill=self.name,
                description="Move a file or folder to another folder.",
                risk=RiskLevel.REVERSIBLE,
                handler=self.move,
                reversible=True,
                params=(
                    Param("source", description="File or folder to move.", is_path=True),
                    Param("destination", description="Destination folder.", is_path=True),
                ),
                summarize=lambda a: f"Move {a['source']} into {a['destination']}",
            ),
            ActionDef(
                name="fs_rename",
                skill=self.name,
                description="Rename a file or folder in place.",
                risk=RiskLevel.REVERSIBLE,
                handler=self.rename,
                reversible=True,
                params=(
                    Param("path", description="File or folder to rename.", is_path=True),
                    Param("new_name", description="New name, without a directory part."),
                ),
                summarize=lambda a: f"Rename {a['path']} to {a['new_name']}",
            ),
            ActionDef(
                name="fs_organize",
                skill=self.name,
                description=(
                    "Tidy a folder by moving its files into subfolders, grouped by "
                    "file type or by last-modified date."
                ),
                risk=RiskLevel.REVERSIBLE,
                handler=self.organize,
                reversible=True,
                bulk=True,
                params=(
                    Param("path", description="Folder to organize.", is_path=True),
                    Param("by", required=False, default="type", enum=("type", "date"),
                          description="Group by file 'type' or by 'date' (YYYY-MM)."),
                ),
                summarize=lambda a: f"Organize {a['path']} by {a.get('by', 'type')}",
            ),
            ActionDef(
                name="fs_delete",
                skill=self.name,
                description=(
                    "Delete a file or folder. Sends it to the Recycle Bin by default "
                    "so it can be restored."
                ),
                risk=RiskLevel.DESTRUCTIVE,
                handler=self.delete,
                reversible=True,
                params=(Param("path", description="File or folder to delete.", is_path=True),),
                summarize=lambda a: f"Delete {a['path']}",
            ),
        )

    def undo_handlers(self) -> dict[str, Callable[[UndoRecord, ExecContext], ActionResult]]:
        return {
            "fs.move": self._undo_move,
            "fs.rename": self._undo_move,
            "fs.organize": self._undo_organize,
            "fs.create_folder": self._undo_create_folder,
            "fs.delete": self._undo_delete,
        }

    # -- read-only ------------------------------------------------------
    def list_folder(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        folder = _expand(args["path"])
        if not folder.is_dir():
            return ActionResult(False, f"{folder} is not a folder.")
        pattern = args.get("pattern") or "*"
        entries = sorted(folder.glob(pattern), key=lambda p: (p.is_file(), p.name.lower()))
        rows = [
            {
                "name": entry.name,
                "kind": "folder" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            }
            for entry in entries
        ]
        return ActionResult(
            True,
            f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} in {folder}.",
            {"path": str(folder), "entries": rows},
        )

    def find(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        folder = _expand(args["path"])
        if not folder.is_dir():
            return ActionResult(False, f"{folder} is not a folder.")
        limit = int(args.get("max_results") or 200)
        matches = []
        for match in folder.rglob(args["pattern"]):
            matches.append(str(match))
            if len(matches) >= limit:
                break
        return ActionResult(
            True,
            f"Found {len(matches)} match(es) for {args['pattern']} under {folder}.",
            {"matches": matches},
        )

    # -- reversible -----------------------------------------------------
    def create_folder(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        folder = _expand(args["path"])
        if folder.exists():
            return ActionResult(True, f"{folder} already exists.", {"created": False})
        if ctx.dry_run:
            return ActionResult(True, f"Would create {folder}.", {"dry_run": True})
        folder.mkdir(parents=True)
        return ActionResult(
            True,
            f"Created {folder}.",
            {"created": True},
            UndoRecord("fs.create_folder", {"path": str(folder)}, f"Remove {folder}"),
        )

    def move(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        source = _expand(args["source"])
        destination = _expand(args["destination"])
        if not source.exists():
            return ActionResult(False, f"{source} does not exist.")
        if destination.exists() and not destination.is_dir():
            return ActionResult(False, f"{destination} is not a folder.")
        target = destination / source.name
        if ctx.dry_run:
            return ActionResult(True, f"Would move {source} to {target}.", {"dry_run": True})
        destination.mkdir(parents=True, exist_ok=True)
        target = _unique_target(target)
        shutil.move(str(source), str(target))
        return ActionResult(
            True,
            f"Moved {source} to {target}.",
            {"source": str(source), "target": str(target)},
            UndoRecord("fs.move", {"from": str(target), "to": str(source)},
                       f"Move {target} back to {source}"),
        )

    def rename(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        source = _expand(args["path"])
        new_name = args["new_name"]
        if "/" in new_name or "\\" in new_name:
            return ActionResult(False, "new_name must not contain a directory part.")
        if not source.exists():
            return ActionResult(False, f"{source} does not exist.")
        target = source.parent / new_name
        if ctx.dry_run:
            return ActionResult(True, f"Would rename {source} to {target}.", {"dry_run": True})
        if target.exists():
            return ActionResult(False, f"{target} already exists.")
        source.rename(target)
        return ActionResult(
            True,
            f"Renamed {source} to {target}.",
            {"source": str(source), "target": str(target)},
            UndoRecord("fs.rename", {"from": str(target), "to": str(source)},
                       f"Rename {target} back to {source.name}"),
        )

    def organize(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        folder = _expand(args["path"])
        mode = args.get("by") or "type"
        if not folder.is_dir():
            return ActionResult(False, f"{folder} is not a folder.")

        planned: list[tuple[Path, Path]] = []
        for entry in sorted(folder.iterdir()):
            if entry.is_dir() or entry.name.startswith("."):
                continue
            if mode == "date":
                bucket = datetime.fromtimestamp(entry.stat().st_mtime).strftime("%Y-%m")
            else:
                bucket = category_for(entry)
            planned.append((entry, folder / bucket / entry.name))

        preview = [{"from": str(src), "to": str(dst)} for src, dst in planned]
        if ctx.dry_run:
            buckets = len({target.parent for _, target in planned})
            return ActionResult(
                True,
                f"Would move {len(planned)} file(s) into {buckets} folder(s).",
                {"dry_run": True, "moves": preview},
            )

        moves: list[dict[str, str]] = []
        for source, target in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            final = _unique_target(target)
            shutil.move(str(source), str(final))
            moves.append({"from": str(source), "to": str(final)})

        return ActionResult(
            True,
            f"Organized {len(moves)} file(s) in {folder} by {mode}.",
            {"moves": moves},
            UndoRecord("fs.organize", {"moves": moves}, f"Undo organizing {folder}"),
        )

    # -- destructive ----------------------------------------------------
    def delete(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        target = _expand(args["path"])
        if not target.exists():
            return ActionResult(False, f"{target} does not exist.")
        use_bin = getattr(ctx.config, "deletes_to_recycle_bin", True)
        if ctx.dry_run:
            where = "the Recycle Bin" if use_bin else "permanent deletion"
            return ActionResult(True, f"Would send {target} to {where}.", {"dry_run": True})
        if not use_bin:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return ActionResult(True, f"Permanently deleted {target}.", {"permanent": True})

        outcome = send_to_trash(target)
        undo = None
        if outcome.restorable:
            undo = UndoRecord(
                "fs.delete",
                {"restore_path": outcome.restore_path, "original": str(target)},
                f"Restore {target}",
            )
        return ActionResult(
            True,
            f"Deleted {target}. {outcome.note}".strip(),
            {"method": outcome.method},
            undo,
        )

    # -- undo -----------------------------------------------------------
    def _undo_move(self, record: UndoRecord, ctx: ExecContext) -> ActionResult:
        source = Path(record.payload["from"])
        target = Path(record.payload["to"])
        if not source.exists():
            return ActionResult(False, f"{source} no longer exists; cannot undo.")
        if target.exists():
            return ActionResult(False, f"{target} already exists; cannot undo.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return ActionResult(True, f"Moved {source} back to {target}.")

    def _undo_organize(self, record: UndoRecord, ctx: ExecContext) -> ActionResult:
        restored, failed = 0, []
        for move in reversed(record.payload.get("moves", [])):
            source, target = Path(move["to"]), Path(move["from"])
            try:
                if not source.exists():
                    raise FileNotFoundError(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(_unique_target(target)))
                restored += 1
            except OSError as exc:
                failed.append(f"{source}: {exc}")
        # Clean up the now-empty category folders we created.
        for move in record.payload.get("moves", []):
            parent = Path(move["to"]).parent
            try:
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
        message = f"Restored {restored} file(s)."
        if failed:
            message += " Could not restore: " + "; ".join(failed)
        return ActionResult(not failed, message)

    def _undo_create_folder(self, record: UndoRecord, ctx: ExecContext) -> ActionResult:
        folder = Path(record.payload["path"])
        if not folder.is_dir():
            return ActionResult(True, f"{folder} is already gone.")
        if any(folder.iterdir()):
            return ActionResult(False, f"{folder} is not empty; leaving it in place.")
        folder.rmdir()
        return ActionResult(True, f"Removed {folder}.")

    def _undo_delete(self, record: UndoRecord, ctx: ExecContext) -> ActionResult:
        try:
            restore_from_trash(record.payload["restore_path"], record.payload["original"])
        except OSError as exc:
            return ActionResult(False, f"Could not restore: {exc}")
        return ActionResult(True, f"Restored {record.payload['original']}.")
