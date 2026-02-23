from __future__ import annotations

"""
Minimal valid SBText program example:

stage
when flag clicked
broadcast [start]
end

sprite Cat
var score
when I receive [start]
set [var score] to (0)
repeat (10)
change [var score] by (1)
move (10) steps
end
say ("done")
end
end

Usage:
python compiler.py input.sbtext output.sb3
"""

import argparse
from pathlib import Path

from codegen import generate_project_json, write_sb3
from parser import Parser
from semantic import analyze


def compile_source(source_text: str, source_dir: Path, output_path: Path) -> None:
    project = Parser.from_source(source_text)
    analyze(project)
    project_json, assets = generate_project_json(project, source_dir=source_dir)
    write_sb3(project_json=project_json, assets=assets, output_path=output_path)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile SBText source into Scratch .sb3")
    parser.add_argument("input", type=Path, help="Path to input .sbtext file")
    parser.add_argument("output", type=Path, help="Path to output .sb3 file")
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    input_path: Path = args.input
    output_path: Path = args.output

    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: '{input_path}'")

    source_text = input_path.read_text(encoding="utf-8")
    compile_source(source_text=source_text, source_dir=input_path.parent, output_path=output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
