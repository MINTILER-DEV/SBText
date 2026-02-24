mod cli;
mod ast;
mod imports;
mod lexer;
mod parser;
mod python_backend;
mod semantic;

use anyhow::Result;
use clap::Parser;
use cli::Args;
use imports::resolve_merged_source;
use lexer::Lexer;
use parser::Parser as SbParser;
use semantic::analyze as semantic_analyze;
use std::path::PathBuf;

fn main() -> Result<()> {
    let args = Args::parse();
    let input = canonicalize_file(&args.input)?;
    let merged = resolve_merged_source(&input)?;
    validate_project(&merged)?;

    if let Some(emit_path) = args.emit_merged {
        std::fs::write(&emit_path, merged.as_bytes())?;
    }

    if !args.no_python_backend {
        let output = args
            .output
            .ok_or_else(|| anyhow::anyhow!("Missing output path. Pass OUTPUT or use --emit-merged only."))?;
        python_backend::compile_with_python(&input, &merged, &output, args.no_svg_scale)?;
    } else if args.output.is_some() {
        return Err(anyhow::anyhow!(
            "OUTPUT was provided but --no-python-backend is set. \
             Either remove OUTPUT or keep Python backend enabled."
        ));
    }

    Ok(())
}

fn validate_project(source: &str) -> Result<()> {
    let mut lexer = Lexer::new(source);
    let tokens = lexer.tokenize()?;
    let mut parser = SbParser::new(tokens);
    let project = parser.parse_project()?;
    semantic_analyze(&project)?;
    Ok(())
}

fn canonicalize_file(path: &PathBuf) -> Result<PathBuf> {
    if !path.exists() || !path.is_file() {
        return Err(anyhow::anyhow!("Input file not found: '{}'.", path.display()));
    }
    Ok(path.canonicalize()?)
}
