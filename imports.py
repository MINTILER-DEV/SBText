from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from parser import CostumeDecl, Parser, Project, Target


class ImportResolutionError(ValueError):
    """Raised when resolving top-level imports fails."""


@dataclass(frozen=True)
class ImportSpec:
    sprite_name: str
    relative_path: str
    line: int


@dataclass
class _ResolvedFile:
    local_targets: list[Target]
    combined_targets: list[Target]


IMPORT_PATTERN = re.compile(
    r'^\s*import\s+\[(?P<name>[^\]\r\n]+)\]\s+from\s+"(?P<path>[^"\r\n]+)"\s*(?:#.*)?$',
    re.IGNORECASE,
)


def resolve_project_from_path(entry_path: Path) -> Project:
    resolved_entry = entry_path.resolve()
    if not resolved_entry.exists() or not resolved_entry.is_file():
        raise ImportResolutionError(f"Input file not found: '{entry_path}'.")
    cache: dict[Path, _ResolvedFile] = {}
    resolved = _resolve_file(path=resolved_entry, stack=[], cache=cache)
    _ensure_unique_sprite_names(resolved.combined_targets)
    return Project(line=1, column=1, targets=list(resolved.combined_targets))


def _resolve_file(path: Path, stack: list[Path], cache: dict[Path, _ResolvedFile]) -> _ResolvedFile:
    cached = cache.get(path)
    if cached is not None:
        return cached
    if path in stack:
        cycle_start = stack.index(path)
        cycle = stack[cycle_start:] + [path]
        cycle_text = " -> ".join(str(p) for p in cycle)
        raise ImportResolutionError(f"Circular import detected: {cycle_text}")

    source = path.read_text(encoding="utf-8")
    imports, stripped_source = _extract_imports(source=source, source_path=path)
    local_targets = _parse_local_targets(stripped_source)
    _normalize_target_asset_paths(local_targets=local_targets, source_dir=path.parent)

    stack.append(path)
    try:
        imported_targets: list[Target] = []
        for spec in imports:
            child_path = (path.parent / spec.relative_path).resolve()
            if not child_path.exists() or not child_path.is_file():
                raise ImportResolutionError(
                    f"Imported file does not exist: '{spec.relative_path}' "
                    f"(from '{path}', line {spec.line})."
                )
            child = _resolve_file(path=child_path, stack=stack, cache=cache)
            _validate_imported_file(spec=spec, source_path=path, child_path=child_path, local_targets=child.local_targets)
            imported_targets.extend(child.combined_targets)
    finally:
        stack.pop()

    resolved = _ResolvedFile(local_targets=local_targets, combined_targets=[*imported_targets, *local_targets])
    cache[path] = resolved
    return resolved


def _extract_imports(source: str, source_path: Path) -> tuple[list[ImportSpec], str]:
    imports: list[ImportSpec] = []
    output_lines: list[str] = []
    saw_non_import_code = False
    lines = source.splitlines(keepends=True)

    for line_no, line in enumerate(lines, start=1):
        current_line = line
        if line_no == 1 and current_line.startswith("\ufeff"):
            current_line = current_line.lstrip("\ufeff")
        stripped_nl = current_line.rstrip("\r\n")
        match = IMPORT_PATTERN.match(stripped_nl)
        if match:
            if saw_non_import_code:
                raise ImportResolutionError(
                    f"Imports are only allowed at the top level. "
                    f"Invalid import in '{source_path}' at line {line_no}."
                )
            sprite_name = match.group("name").strip()
            relative_path = match.group("path").strip()
            if not sprite_name:
                raise ImportResolutionError(f"Import sprite name cannot be empty in '{source_path}' at line {line_no}.")
            if not relative_path:
                raise ImportResolutionError(f"Import path cannot be empty in '{source_path}' at line {line_no}.")
            imports.append(ImportSpec(sprite_name=sprite_name, relative_path=relative_path, line=line_no))
            output_lines.append("\n" if current_line.endswith("\n") else "")
            continue

        if not _is_blank_or_comment(stripped_nl):
            saw_non_import_code = True
        output_lines.append(current_line)
    return imports, "".join(output_lines)


def _parse_local_targets(source: str) -> list[Target]:
    if not any(not _is_blank_or_comment(line) for line in source.splitlines()):
        return []
    project = Parser.from_source(source)
    return list(project.targets)


def _validate_imported_file(spec: ImportSpec, source_path: Path, child_path: Path, local_targets: list[Target]) -> None:
    local_sprites = [target for target in local_targets if not target.is_stage]
    if len(local_sprites) == 0:
        raise ImportResolutionError(
            f"Imported file '{child_path}' defines zero sprites; expected exactly one "
            f"(imported from '{source_path}', line {spec.line})."
        )
    if len(local_sprites) > 1:
        raise ImportResolutionError(
            f"Imported file '{child_path}' defines more than one sprite; expected exactly one "
            f"(imported from '{source_path}', line {spec.line})."
        )
    if any(target.is_stage for target in local_targets):
        raise ImportResolutionError(
            f"Imported file '{child_path}' must not define a stage "
            f"(imported from '{source_path}', line {spec.line})."
        )
    actual = local_sprites[0].name
    if actual != spec.sprite_name:
        raise ImportResolutionError(
            f"Imported sprite name mismatch in '{source_path}', line {spec.line}: "
            f"expected '{spec.sprite_name}', file defines '{actual}'."
        )


def _ensure_unique_sprite_names(targets: list[Target]) -> None:
    seen: dict[str, str] = {}
    for target in targets:
        if target.is_stage:
            continue
        lowered = target.name.lower()
        if lowered in seen:
            raise ImportResolutionError(f"Duplicate sprite name in final project: '{target.name}'.")
        seen[lowered] = target.name


def _normalize_target_asset_paths(local_targets: list[Target], source_dir: Path) -> None:
    for target in local_targets:
        normalized_costumes: list[CostumeDecl] = []
        for costume in target.costumes:
            costume_path = Path(costume.path)
            if costume_path.is_absolute():
                normalized_costumes.append(costume)
            else:
                candidates = [
                    (source_dir / costume_path).resolve(),
                    (source_dir.parent / costume_path).resolve(),
                    (Path.cwd() / costume_path).resolve(),
                ]
                absolute_path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
                normalized_costumes.append(CostumeDecl(line=costume.line, column=costume.column, path=str(absolute_path)))
        target.costumes = normalized_costumes


def _is_blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return stripped == "" or stripped.startswith("#")
