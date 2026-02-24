# sbtext-rs

Native Rust entrypoint for SBText with import resolution/validation implemented in Rust.

Current state:

- Resolves `import [SpriteName] from "path.sbtext"` recursively.
- Enforces top-level-only imports.
- Detects circular imports.
- Enforces imported-file sprite constraints and final duplicate sprite-name constraints.
- Can emit merged source via `--emit-merged`.
- Can invoke the existing Python backend for `.sb3` generation (transition mode).

## Build

```bash
cargo build --release
```

## Usage

```bash
sbtext-rs INPUT OUTPUT
sbtext-rs INPUT OUTPUT --no-svg-scale
sbtext-rs INPUT --emit-merged merged.sbtext --no-python-backend
```
