from __future__ import annotations

from dataclasses import dataclass, field

from lexer import Lexer, Token


class ParseError(ValueError):
    """Raised when parsing fails."""


@dataclass
class Node:
    line: int
    column: int


@dataclass
class Expr(Node):
    pass


@dataclass
class NumberExpr(Expr):
    value: float


@dataclass
class StringExpr(Expr):
    value: str


@dataclass
class VarExpr(Expr):
    name: str


@dataclass
class UnaryExpr(Expr):
    op: str
    operand: Expr


@dataclass
class BinaryExpr(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Statement(Node):
    pass


@dataclass
class BroadcastStmt(Statement):
    message: str


@dataclass
class SetVarStmt(Statement):
    var_name: str
    value: Expr


@dataclass
class ChangeVarStmt(Statement):
    var_name: str
    delta: Expr


@dataclass
class MoveStmt(Statement):
    steps: Expr


@dataclass
class SayStmt(Statement):
    message: Expr


@dataclass
class RepeatStmt(Statement):
    times: Expr
    body: list[Statement] = field(default_factory=list)


@dataclass
class IfStmt(Statement):
    condition: Expr
    then_body: list[Statement] = field(default_factory=list)
    else_body: list[Statement] = field(default_factory=list)


@dataclass
class ProcedureCallStmt(Statement):
    name: str
    args: list[Expr] = field(default_factory=list)


@dataclass
class EventScript(Node):
    event_type: str
    message: str | None
    body: list[Statement] = field(default_factory=list)


@dataclass
class Procedure(Node):
    name: str
    params: list[str] = field(default_factory=list)
    body: list[Statement] = field(default_factory=list)


@dataclass
class CostumeDecl(Node):
    path: str


@dataclass
class VariableDecl(Node):
    name: str


@dataclass
class Target(Node):
    name: str
    is_stage: bool
    variables: list[VariableDecl] = field(default_factory=list)
    costumes: list[CostumeDecl] = field(default_factory=list)
    procedures: list[Procedure] = field(default_factory=list)
    scripts: list[EventScript] = field(default_factory=list)


@dataclass
class Project(Node):
    targets: list[Target] = field(default_factory=list)


class Parser:
    _EXPR_PRECEDENCE = {
        "or": 1,
        "and": 2,
        "=": 3,
        "==": 3,
        "!=": 3,
        "<": 3,
        "<=": 3,
        ">": 3,
        ">=": 3,
        "+": 4,
        "-": 4,
        "*": 5,
        "/": 5,
        "%": 5,
    }

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    @classmethod
    def from_source(cls, source: str) -> Project:
        tokens = Lexer(source).tokenize()
        return cls(tokens).parse_project()

    def parse_project(self) -> Project:
        self._skip_newlines()
        line, col = self._current().line, self._current().column
        targets: list[Target] = []
        while not self._at_end():
            token = self._current()
            if self._match_keyword("sprite"):
                targets.append(self._parse_sprite(token.line, token.column))
            elif self._match_keyword("stage"):
                targets.append(self._parse_stage(token.line, token.column))
            else:
                self._error_here("Expected 'sprite' or 'stage'.")
            self._skip_newlines()
        if not targets:
            raise ParseError("Expected at least one 'stage' or 'sprite' block.")
        return Project(line=line, column=col, targets=targets)

    def _parse_sprite(self, line: int, col: int) -> Target:
        name = self._parse_name_token()
        self._skip_newlines()
        return self._parse_target_body(name=name, is_stage=False, line=line, col=col)

    def _parse_stage(self, line: int, col: int) -> Target:
        name = "Stage"
        if self._check_type("IDENT") or self._check_type("STRING"):
            name = self._parse_name_token()
        self._skip_newlines()
        return self._parse_target_body(name=name, is_stage=True, line=line, col=col)

    def _parse_target_body(self, name: str, is_stage: bool, line: int, col: int) -> Target:
        target = Target(line=line, column=col, name=name, is_stage=is_stage)
        while True:
            self._skip_newlines()
            if self._at_end():
                self._error_here(f"Unterminated target block for '{name}'. Expected 'end'.")
            if self._match_keyword("end"):
                break
            if self._match_keyword("var"):
                var_line, var_col = self._previous().line, self._previous().column
                var_name = self._parse_name_token()
                target.variables.append(VariableDecl(line=var_line, column=var_col, name=var_name))
                continue
            if self._match_keyword("costume"):
                costume_line, costume_col = self._previous().line, self._previous().column
                path_token = self._consume_type("STRING", "Expected costume path string.")
                target.costumes.append(CostumeDecl(line=costume_line, column=costume_col, path=path_token.value))
                continue
            if self._match_keyword("define"):
                target.procedures.append(self._parse_procedure(self._previous().line, self._previous().column))
                continue
            if self._match_keyword("when"):
                target.scripts.append(self._parse_event_script(self._previous().line, self._previous().column))
                continue
            self._error_here("Expected 'var', 'costume', 'define', 'when', or 'end' inside target.")
        return target

    def _parse_procedure(self, line: int, col: int) -> Procedure:
        name = self._parse_name_token()
        params: list[str] = []
        while self._check_type("LPAREN"):
            self._consume_type("LPAREN", "Expected '('.")
            if self._check_type("RPAREN"):
                self._error_here("Empty parameter declaration is not allowed.")
            param = self._parse_name_token()
            self._consume_type("RPAREN", "Expected ')' after parameter name.")
            params.append(param)
        self._skip_newlines()
        body = self._parse_statement_block(until_keywords={"end"})
        self._consume_keyword("end", "Expected 'end' to close procedure definition.")
        return Procedure(line=line, column=col, name=name, params=params, body=body)

    def _parse_event_script(self, line: int, col: int) -> EventScript:
        event_type: str
        message: str | None = None
        if self._match_keyword("flag"):
            self._consume_keyword("clicked", "Expected 'clicked' after 'when flag'.")
            event_type = "when_flag_clicked"
        elif self._match_keyword("this"):
            self._consume_keyword("sprite", "Expected 'sprite' in 'when this sprite clicked'.")
            self._consume_keyword("clicked", "Expected 'clicked' in 'when this sprite clicked'.")
            event_type = "when_this_sprite_clicked"
        elif self._match_keyword("i"):
            self._consume_keyword("receive", "Expected 'receive' after 'when I'.")
            message = self._parse_bracket_text()
            if not message:
                self._error_here("Broadcast message cannot be empty.")
            event_type = "when_i_receive"
        else:
            self._error_here("Unknown event header after 'when'.")
            raise AssertionError("unreachable")
        self._skip_newlines()
        body = self._parse_statement_block(
            until_keywords={"when", "define", "var", "costume", "end"},
            consume_until=False,
        )
        # Allow optional explicit `end` after event scripts while preserving
        # existing open-ended script parsing where target `end` closes the block.
        if self._check_keyword("end") and self._looks_like_event_end():
            self._advance()
        return EventScript(line=line, column=col, event_type=event_type, message=message, body=body)

    def _parse_statement_block(
        self,
        until_keywords: set[str],
        consume_until: bool = False,
    ) -> list[Statement]:
        statements: list[Statement] = []
        while True:
            self._skip_newlines()
            if self._at_end():
                break
            token = self._current()
            if token.type == "KEYWORD" and token.value in until_keywords:
                if consume_until:
                    self._advance()
                break
            statements.append(self._parse_statement())
        return statements

    def _parse_statement(self) -> Statement:
        token = self._current()
        if token.type == "KEYWORD":
            if token.value == "broadcast":
                return self._parse_broadcast_stmt()
            if token.value == "set":
                return self._parse_set_stmt()
            if token.value == "change":
                return self._parse_change_stmt()
            if token.value == "move":
                return self._parse_move_stmt()
            if token.value == "say":
                return self._parse_say_stmt()
            if token.value == "repeat":
                return self._parse_repeat_stmt()
            if token.value == "if":
                return self._parse_if_stmt()
        if token.type == "IDENT":
            return self._parse_call_stmt()
        self._error_here("Unknown statement.")
        raise AssertionError("unreachable")

    def _parse_broadcast_stmt(self) -> BroadcastStmt:
        start = self._consume_keyword("broadcast", "Expected 'broadcast'.")
        message = self._parse_bracket_text()
        if not message:
            self._error_here("Broadcast message cannot be empty.")
        return BroadcastStmt(line=start.line, column=start.column, message=message)

    def _parse_set_stmt(self) -> SetVarStmt:
        start = self._consume_keyword("set", "Expected 'set'.")
        var_name = self._parse_variable_field_name()
        self._consume_keyword("to", "Expected 'to' in set statement.")
        value = self._parse_wrapped_expression()
        return SetVarStmt(line=start.line, column=start.column, var_name=var_name, value=value)

    def _parse_change_stmt(self) -> ChangeVarStmt:
        start = self._consume_keyword("change", "Expected 'change'.")
        var_name = self._parse_variable_field_name()
        self._consume_keyword("by", "Expected 'by' in change statement.")
        value = self._parse_wrapped_expression()
        return ChangeVarStmt(line=start.line, column=start.column, var_name=var_name, delta=value)

    def _parse_move_stmt(self) -> MoveStmt:
        start = self._consume_keyword("move", "Expected 'move'.")
        steps = self._parse_wrapped_expression()
        self._match_keyword("steps")  # optional
        return MoveStmt(line=start.line, column=start.column, steps=steps)

    def _parse_say_stmt(self) -> SayStmt:
        start = self._consume_keyword("say", "Expected 'say'.")
        expr = self._parse_wrapped_expression()
        return SayStmt(line=start.line, column=start.column, message=expr)

    def _parse_repeat_stmt(self) -> RepeatStmt:
        start = self._consume_keyword("repeat", "Expected 'repeat'.")
        times = self._parse_wrapped_expression()
        self._skip_newlines()
        body = self._parse_statement_block(until_keywords={"end"})
        self._consume_keyword("end", "Expected 'end' to close repeat block.")
        return RepeatStmt(line=start.line, column=start.column, times=times, body=body)

    def _parse_if_stmt(self) -> IfStmt:
        start = self._consume_keyword("if", "Expected 'if'.")
        condition_tokens = self._collect_tokens_until_keyword("then")
        if not condition_tokens:
            raise ParseError(f"Expected condition after 'if' at line {start.line}, column {start.column}.")
        if condition_tokens[0].type == "OP" and condition_tokens[0].value == "<":
            if not (condition_tokens[-1].type == "OP" and condition_tokens[-1].value == ">"):
                raise ParseError(
                    f"Expected condition enclosed in '<...>' before 'then' at line {start.line}, column {start.column}."
                )
            condition_tokens = condition_tokens[1:-1]
        condition = self._parse_expression_from_tokens(condition_tokens)
        self._consume_keyword("then", "Expected 'then' in if statement.")
        self._skip_newlines()
        then_body = self._parse_statement_block(until_keywords={"else", "end"})
        else_body: list[Statement] = []
        if self._match_keyword("else"):
            self._skip_newlines()
            else_body = self._parse_statement_block(until_keywords={"end"})
        self._consume_keyword("end", "Expected 'end' to close if statement.")
        return IfStmt(line=start.line, column=start.column, condition=condition, then_body=then_body, else_body=else_body)

    def _parse_call_stmt(self) -> ProcedureCallStmt:
        name = self._consume_type("IDENT", "Expected procedure name.")
        args: list[Expr] = []
        while self._check_type("LPAREN"):
            args.append(self._parse_wrapped_expression())
        return ProcedureCallStmt(line=name.line, column=name.column, name=name.value, args=args)

    def _parse_wrapped_expression(self) -> Expr:
        self._consume_type("LPAREN", "Expected '('.")
        expr = self._parse_expression(stop_types={"RPAREN"})
        self._consume_type("RPAREN", "Expected ')' after expression.")
        return expr

    def _parse_expression_from_tokens(self, tokens: list[Token]) -> Expr:
        synthetic = tokens + [Token(type="EOF", value="", line=tokens[-1].line, column=tokens[-1].column)]
        parser = Parser(synthetic)
        expr = parser._parse_expression(stop_types={"EOF"})
        parser._consume_type("EOF", "Unexpected trailing tokens in expression.")
        return expr

    def _parse_expression(self, stop_types: set[str] | None = None, min_precedence: int = 1) -> Expr:
        if stop_types is None:
            stop_types = set()
        left = self._parse_unary(stop_types=stop_types)
        while True:
            token = self._current()
            if token.type in stop_types:
                break
            op = self._as_operator(token)
            if op is None:
                break
            precedence = self._EXPR_PRECEDENCE.get(op)
            if precedence is None or precedence < min_precedence:
                break
            self._advance()
            right = self._parse_expression(stop_types=stop_types, min_precedence=precedence + 1)
            left = BinaryExpr(line=token.line, column=token.column, op=op, left=left, right=right)
        return left

    def _parse_unary(self, stop_types: set[str]) -> Expr:
        token = self._current()
        if token.type == "OP" and token.value == "-":
            self._advance()
            operand = self._parse_unary(stop_types)
            return UnaryExpr(line=token.line, column=token.column, op="-", operand=operand)
        if token.type == "KEYWORD" and token.value == "not":
            self._advance()
            operand = self._parse_unary(stop_types)
            return UnaryExpr(line=token.line, column=token.column, op="not", operand=operand)
        return self._parse_primary(stop_types=stop_types)

    def _parse_primary(self, stop_types: set[str]) -> Expr:
        token = self._current()
        if token.type in stop_types:
            self._error_here("Expected expression.")
        if token.type == "NUMBER":
            self._advance()
            value = float(token.value)
            return NumberExpr(line=token.line, column=token.column, value=value)
        if token.type == "STRING":
            self._advance()
            return StringExpr(line=token.line, column=token.column, value=token.value)
        if token.type == "IDENT":
            if self._peek().type == "LPAREN":
                raise ParseError(
                    f"Procedure call '{token.value}' cannot appear inside an expression at line {token.line}, column {token.column}."
                )
            self._advance()
            return VarExpr(line=token.line, column=token.column, name=token.value)
        if token.type == "LPAREN":
            self._advance()
            expr = self._parse_expression(stop_types={"RPAREN"})
            self._consume_type("RPAREN", "Expected ')' after grouped expression.")
            return expr
        if token.type == "LBRACKET":
            name = self._parse_variable_field_name()
            return VarExpr(line=token.line, column=token.column, name=name)
        self._error_here("Expected expression.")
        raise AssertionError("unreachable")

    def _parse_variable_field_name(self) -> str:
        contents = self._parse_bracket_tokens()
        if not contents:
            self._error_here("Variable name cannot be empty.")
        parts = [t.value for t in contents]
        if parts[0].lower() == "var":
            parts = parts[1:]
        name = " ".join(parts).strip()
        if not name:
            self._error_here("Variable name cannot be empty.")
        return name

    def _parse_bracket_text(self) -> str:
        contents = self._parse_bracket_tokens()
        return " ".join(token.value for token in contents).strip()

    def _parse_bracket_tokens(self) -> list[Token]:
        self._consume_type("LBRACKET", "Expected '['.")
        tokens: list[Token] = []
        while not self._at_end() and not self._check_type("RBRACKET"):
            if self._check_type("NEWLINE"):
                self._error_here("Unexpected newline in bracket expression.")
            tokens.append(self._advance())
        self._consume_type("RBRACKET", "Expected ']'.")
        return tokens

    def _collect_tokens_until_keyword(self, keyword: str) -> list[Token]:
        out: list[Token] = []
        depth_paren = 0
        depth_bracket = 0
        while not self._at_end():
            token = self._current()
            if token.type == "KEYWORD" and token.value == keyword and depth_paren == 0 and depth_bracket == 0:
                break
            if token.type == "LPAREN":
                depth_paren += 1
            elif token.type == "RPAREN":
                depth_paren -= 1
            elif token.type == "LBRACKET":
                depth_bracket += 1
            elif token.type == "RBRACKET":
                depth_bracket -= 1
            out.append(self._advance())
        if depth_paren != 0 or depth_bracket != 0:
            self._error_here("Unbalanced delimiters while reading condition.")
        return out

    def _parse_name_token(self) -> str:
        token = self._current()
        if token.type == "IDENT":
            self._advance()
            return token.value
        if token.type == "STRING":
            self._advance()
            return token.value
        self._error_here("Expected name.")
        raise AssertionError("unreachable")

    def _as_operator(self, token: Token) -> str | None:
        if token.type == "OP":
            return token.value
        if token.type == "KEYWORD" and token.value in {"and", "or"}:
            return token.value
        return None

    def _check_keyword(self, value: str) -> bool:
        token = self._current()
        return token.type == "KEYWORD" and token.value == value

    def _looks_like_event_end(self) -> bool:
        # If the next significant token starts a new top-level target or EOF,
        # treat current `end` as target terminator, not event terminator.
        idx = self.index + 1
        while idx < len(self.tokens) and self.tokens[idx].type == "NEWLINE":
            idx += 1
        if idx >= len(self.tokens):
            return False
        token = self.tokens[idx]
        if token.type == "EOF":
            return False
        if token.type == "KEYWORD" and token.value in {"sprite", "stage"}:
            return False
        return True

    def _consume_keyword(self, value: str, message: str) -> Token:
        token = self._current()
        if token.type == "KEYWORD" and token.value == value:
            self._advance()
            return token
        raise ParseError(f"{message} (line {token.line}, column {token.column})")

    def _consume_type(self, token_type: str, message: str) -> Token:
        token = self._current()
        if token.type == token_type:
            self._advance()
            return token
        raise ParseError(f"{message} (line {token.line}, column {token.column})")

    def _match_keyword(self, value: str) -> bool:
        token = self._current()
        if token.type == "KEYWORD" and token.value == value:
            self._advance()
            return True
        return False

    def _check_type(self, token_type: str) -> bool:
        return self._current().type == token_type

    def _skip_newlines(self) -> None:
        while self._check_type("NEWLINE"):
            self._advance()

    def _at_end(self) -> bool:
        return self._current().type == "EOF"

    def _current(self) -> Token:
        return self.tokens[self.index]

    def _peek(self) -> Token:
        if self.index + 1 >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[self.index + 1]

    def _previous(self) -> Token:
        return self.tokens[self.index - 1]

    def _advance(self) -> Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _error_here(self, message: str) -> None:
        token = self._current()
        raise ParseError(f"{message} (line {token.line}, column {token.column})")
