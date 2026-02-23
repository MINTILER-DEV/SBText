from __future__ import annotations

from dataclasses import dataclass


class LexerError(ValueError):
    """Raised when tokenization fails."""


@dataclass(frozen=True)
class Token:
    type: str
    value: str
    line: int
    column: int


KEYWORDS = {
    "and",
    "broadcast",
    "by",
    "change",
    "clicked",
    "costume",
    "define",
    "else",
    "end",
    "flag",
    "i",
    "if",
    "move",
    "not",
    "or",
    "receive",
    "repeat",
    "say",
    "set",
    "sprite",
    "stage",
    "steps",
    "then",
    "this",
    "to",
    "var",
    "when",
}


SYMBOLS = {
    "(": "LPAREN",
    ")": "RPAREN",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ",": "COMMA",
}


class Lexer:
    def __init__(self, source: str) -> None:
        self.source = source
        self.length = len(source)
        self.index = 0
        self.line = 1
        self.column = 1

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while not self._at_end():
            ch = self._peek()
            if ch == "\ufeff":
                self._advance()
                continue
            if ch in (" ", "\t", "\r"):
                self._advance()
                continue
            if ch == "\n":
                tokens.append(Token("NEWLINE", "\n", self.line, self.column))
                self._advance()
                continue
            if ch == "#":
                self._skip_comment()
                continue
            if ch == '"':
                tokens.append(self._read_string())
                continue
            if ch.isdigit():
                tokens.append(self._read_number())
                continue
            if ch.isalpha() or ch == "_":
                tokens.append(self._read_identifier())
                continue
            if ch in SYMBOLS:
                line, col = self.line, self.column
                token_type = SYMBOLS[ch]
                self._advance()
                tokens.append(Token(token_type, ch, line, col))
                continue
            if ch in {"+", "-", "*", "/", "%"}:
                line, col = self.line, self.column
                self._advance()
                tokens.append(Token("OP", ch, line, col))
                continue
            if ch in {"=", "!", "<", ">"}:
                tokens.append(self._read_operator())
                continue
            raise LexerError(f"Unexpected character {ch!r} at line {self.line}, column {self.column}.")
        tokens.append(Token("EOF", "", self.line, self.column))
        return tokens

    def _read_operator(self) -> Token:
        line, col = self.line, self.column
        ch = self._advance()
        if ch in {"=", "!", "<", ">"} and self._peek() == "=":
            op = ch + self._advance()
            return Token("OP", op, line, col)
        return Token("OP", ch, line, col)

    def _read_identifier(self) -> Token:
        line, col = self.line, self.column
        chars = [self._advance()]
        while not self._at_end():
            ch = self._peek()
            if ch.isalnum() or ch == "_":
                chars.append(self._advance())
            else:
                break
        value = "".join(chars)
        lowered = value.lower()
        if lowered in KEYWORDS:
            return Token("KEYWORD", lowered, line, col)
        return Token("IDENT", value, line, col)

    def _read_number(self) -> Token:
        line, col = self.line, self.column
        chars = [self._advance()]
        seen_dot = False
        while not self._at_end():
            ch = self._peek()
            if ch.isdigit():
                chars.append(self._advance())
                continue
            if ch == "." and not seen_dot:
                seen_dot = True
                chars.append(self._advance())
                continue
            break
        return Token("NUMBER", "".join(chars), line, col)

    def _read_string(self) -> Token:
        line, col = self.line, self.column
        self._advance()  # opening quote
        chars: list[str] = []
        while not self._at_end():
            ch = self._advance()
            if ch == '"':
                return Token("STRING", "".join(chars), line, col)
            if ch == "\\":
                if self._at_end():
                    break
                esc = self._advance()
                escapes = {
                    '"': '"',
                    "\\": "\\",
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                }
                chars.append(escapes.get(esc, esc))
                continue
            if ch == "\n":
                raise LexerError(f"Unterminated string literal at line {line}, column {col}.")
            chars.append(ch)
        raise LexerError(f"Unterminated string literal at line {line}, column {col}.")

    def _skip_comment(self) -> None:
        while not self._at_end() and self._peek() != "\n":
            self._advance()

    def _peek(self) -> str:
        if self._at_end():
            return "\0"
        return self.source[self.index]

    def _advance(self) -> str:
        ch = self.source[self.index]
        self.index += 1
        if ch == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return ch

    def _at_end(self) -> bool:
        return self.index >= self.length
