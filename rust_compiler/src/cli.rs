use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(
    name = "sbtext-rs",
    about = "Rust entrypoint for SBText compilation (import resolution in Rust, Python backend optional)."
)]
pub struct Args {
    #[arg(value_name = "INPUT")]
    pub input: PathBuf,

    #[arg(value_name = "OUTPUT")]
    pub output: Option<PathBuf>,

    #[arg(long, help = "Disable automatic SVG normalization to 64x64 (forwarded to Python backend).")]
    pub no_svg_scale: bool,

    #[arg(long, help = "Write merged source after resolving imports to this path.")]
    pub emit_merged: Option<PathBuf>,

    #[arg(long, help = "Use native Rust backend for .sb3 output instead of Python backend.")]
    pub no_python_backend: bool,
}
