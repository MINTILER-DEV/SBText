from __future__ import annotations

from dataclasses import dataclass

from parser import (
    BinaryExpr,
    BroadcastStmt,
    ChangeVarStmt,
    EventScript,
    Expr,
    IfStmt,
    MoveStmt,
    Procedure,
    ProcedureCallStmt,
    Project,
    RepeatStmt,
    SayStmt,
    SetVarStmt,
    Statement,
    Target,
    UnaryExpr,
    VarExpr,
)


class SemanticError(ValueError):
    """Raised when semantic validation fails."""


@dataclass
class ProcedureInfo:
    name: str
    line: int
    params: list[str]


def analyze(project: Project) -> None:
    if not project.targets:
        raise SemanticError("Project must define at least one target.")
    stage_count = sum(1 for target in project.targets if target.is_stage)
    if stage_count == 0:
        raise SemanticError("Project must define a 'stage' block.")
    if stage_count > 1:
        raise SemanticError("Project can only define one stage.")
    names = set()
    for target in project.targets:
        lowered = target.name.lower()
        if lowered in names:
            raise SemanticError(f"Duplicate target name '{target.name}' at line {target.line}.")
        names.add(lowered)
        _analyze_target(target)


def _analyze_target(target: Target) -> None:
    variables: dict[str, int] = {}
    for decl in target.variables:
        lowered = decl.name.lower()
        if lowered in variables:
            raise SemanticError(
                f"Duplicate variable '{decl.name}' in target '{target.name}' at line {decl.line}, column {decl.column}."
            )
        variables[lowered] = decl.line

    procedures: dict[str, ProcedureInfo] = {}
    for procedure in target.procedures:
        lowered = procedure.name.lower()
        if lowered in procedures:
            prev = procedures[lowered]
            raise SemanticError(
                f"Procedure '{procedure.name}' is already defined at line {prev.line} in target '{target.name}'."
            )
        if len(set(p.lower() for p in procedure.params)) != len(procedure.params):
            raise SemanticError(
                f"Procedure '{procedure.name}' has duplicate parameter names at line {procedure.line}, column {procedure.column}."
            )
        procedures[lowered] = ProcedureInfo(name=procedure.name, line=procedure.line, params=procedure.params)

    for procedure in target.procedures:
        params = {name.lower() for name in procedure.params}
        _analyze_statements(
            target=target,
            statements=procedure.body,
            variables=variables,
            procedures=procedures,
            param_scope=params,
            current_line=procedure.line,
            scope_name=f"procedure '{procedure.name}'",
        )

    for script in target.scripts:
        _analyze_event_script(
            target=target,
            script=script,
            variables=variables,
            procedures=procedures,
        )


def _analyze_event_script(
    target: Target,
    script: EventScript,
    variables: dict[str, int],
    procedures: dict[str, ProcedureInfo],
) -> None:
    _analyze_statements(
        target=target,
        statements=script.body,
        variables=variables,
        procedures=procedures,
        param_scope=set(),
        current_line=script.line,
        scope_name=f"event script '{script.event_type}'",
    )


def _analyze_statements(
    target: Target,
    statements: list[Statement],
    variables: dict[str, int],
    procedures: dict[str, ProcedureInfo],
    param_scope: set[str],
    current_line: int,
    scope_name: str,
) -> None:
    for stmt in statements:
        if isinstance(stmt, BroadcastStmt):
            if not stmt.message:
                raise SemanticError(
                    f"Broadcast message cannot be empty at line {stmt.line}, column {stmt.column} in target '{target.name}'."
                )
        elif isinstance(stmt, SetVarStmt):
            _ensure_variable_exists(target, stmt.var_name, variables, param_scope, stmt.line, stmt.column)
            _analyze_expr(target, stmt.value, variables, param_scope)
        elif isinstance(stmt, ChangeVarStmt):
            _ensure_variable_exists(target, stmt.var_name, variables, param_scope, stmt.line, stmt.column)
            _analyze_expr(target, stmt.delta, variables, param_scope)
        elif isinstance(stmt, MoveStmt):
            _analyze_expr(target, stmt.steps, variables, param_scope)
        elif isinstance(stmt, SayStmt):
            _analyze_expr(target, stmt.message, variables, param_scope)
        elif isinstance(stmt, RepeatStmt):
            _analyze_expr(target, stmt.times, variables, param_scope)
            _analyze_statements(
                target=target,
                statements=stmt.body,
                variables=variables,
                procedures=procedures,
                param_scope=param_scope,
                current_line=current_line,
                scope_name=scope_name,
            )
        elif isinstance(stmt, IfStmt):
            _analyze_expr(target, stmt.condition, variables, param_scope)
            _analyze_statements(
                target=target,
                statements=stmt.then_body,
                variables=variables,
                procedures=procedures,
                param_scope=param_scope,
                current_line=current_line,
                scope_name=scope_name,
            )
            _analyze_statements(
                target=target,
                statements=stmt.else_body,
                variables=variables,
                procedures=procedures,
                param_scope=param_scope,
                current_line=current_line,
                scope_name=scope_name,
            )
        elif isinstance(stmt, ProcedureCallStmt):
            proc = procedures.get(stmt.name.lower())
            if proc is None:
                raise SemanticError(
                    f"Unknown procedure '{stmt.name}' at line {stmt.line}, column {stmt.column} in target '{target.name}'."
                )
            if stmt.line < proc.line:
                raise SemanticError(
                    f"Procedure '{stmt.name}' is used before it is defined (call line {stmt.line}, definition line {proc.line}) "
                    f"in target '{target.name}'."
                )
            if len(stmt.args) != len(proc.params):
                raise SemanticError(
                    f"Procedure '{stmt.name}' expects {len(proc.params)} argument(s), got {len(stmt.args)} at line {stmt.line}, "
                    f"column {stmt.column} in {scope_name}."
                )
            for expr in stmt.args:
                _analyze_expr(target, expr, variables, param_scope)
        else:
            raise SemanticError(
                f"Unsupported statement type '{type(stmt).__name__}' at line {stmt.line}, column {stmt.column} in target '{target.name}'."
            )


def _analyze_expr(target: Target, expr: Expr, variables: dict[str, int], param_scope: set[str]) -> None:
    if isinstance(expr, VarExpr):
        lowered = expr.name.lower()
        if lowered in param_scope:
            return
        if lowered not in variables:
            raise SemanticError(
                f"Unknown variable '{expr.name}' at line {expr.line}, column {expr.column} in target '{target.name}'."
            )
        return
    if isinstance(expr, UnaryExpr):
        _analyze_expr(target, expr.operand, variables, param_scope)
        return
    if isinstance(expr, BinaryExpr):
        _analyze_expr(target, expr.left, variables, param_scope)
        _analyze_expr(target, expr.right, variables, param_scope)
        return


def _ensure_variable_exists(
    target: Target,
    name: str,
    variables: dict[str, int],
    param_scope: set[str],
    line: int,
    column: int,
) -> None:
    lowered = name.lower()
    if lowered in param_scope:
        raise SemanticError(
            f"Variable field '{name}' refers to a procedure parameter at line {line}, column {column}; "
            "Scratch variable blocks must target declared variables."
        )
    if lowered not in variables:
        raise SemanticError(f"Unknown variable '{name}' at line {line}, column {column} in target '{target.name}'.")
