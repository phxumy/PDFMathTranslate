"""Conservative detection of source-code listings before paragraph reflow."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from pdfminer.layout import LTChar

_CODE_FONT_RE = re.compile(
    r"consolas|courier|mono|code|menlo|lucidaconsole|typewriter|cmtt|inconsolata",
    re.IGNORECASE,
)
_MATH_CALLS = frozenset(
    {"sin", "cos", "tan", "log", "exp", "sqrt", "min", "max", "abs", "sum"}
)


@dataclass
class _CodeLine:
    chars: list[LTChar] = field(default_factory=list)

    @property
    def size(self) -> float:
        return float(self.chars[0].size)

    @property
    def y(self) -> float:
        return float(self.chars[0].y0)

    @property
    def text(self) -> str:
        result = ""
        previous = None
        for char in sorted(self.chars, key=lambda value: value.x0):
            if previous is not None and char.x0 - previous.x1 > 0.3 * self.size:
                result += " "
            result += char.get_text()
            previous = char
        return result.strip()


def _statement_evidence(text: str) -> tuple[bool, bool]:
    """Return (statement, strong evidence); equations alone are never strong."""
    if text.endswith(":") and re.match(r"(?:for|while|if|elif|def|class|with)\b", text):
        # A statement header can only be parsed with a temporary suite.
        source = re.sub(r"^elif\b", "if", text) + "\n    pass"
    else:
        source = text
    try:
        body = ast.parse(source).body
    except (SyntaxError, ValueError):
        return False, False
    if len(body) != 1:
        return False, False
    node = body[0]
    if isinstance(
        node,
        (
            ast.For,
            ast.While,
            ast.If,
            ast.With,
            ast.FunctionDef,
            ast.ClassDef,
            ast.Import,
            ast.ImportFrom,
        ),
    ):
        return True, True
    if not isinstance(
        node,
        (
            ast.Assign,
            ast.AugAssign,
            ast.AnnAssign,
            ast.Expr,
            ast.Return,
            ast.Break,
            ast.Continue,
            ast.Raise,
        ),
    ):
        return False, False
    calls = [value.func for value in ast.walk(node) if isinstance(value, ast.Call)]
    strong = any(
        isinstance(call, ast.Attribute)
        or (
            isinstance(call, ast.Name)
            and len(call.id) > 2
            and call.id not in _MATH_CALLS
        )
        for call in calls
    )
    # DataFrame/array indexing with a member name is distinctly code-like;
    # simple a[i] and x = y remain only weak evidence.
    strong = strong or any(
        isinstance(value, ast.Subscript) and isinstance(value.value, ast.Attribute)
        for value in ast.walk(node)
    )
    if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Call):
        return False, False
    return True, strong


def code_block_chars(children) -> set[LTChar]:
    """Identify multi-line monospaced code, including its comment continuations.

    Font names alone must not protect ordinary prose, reference entries or
    equations. Require at least three valid statements, including two with
    programming-specific syntax, in one spatially continuous font-size group.
    This intentionally leaves ambiguous snippets for the ordinary layout path.
    The original glyphs, not reconstructed text, are returned for preservation.
    """
    chars = []
    for child in children:
        if not isinstance(child, LTChar) or not getattr(child, "upright", True):
            continue
        name = child.fontname
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        if _CODE_FONT_RE.search(name) and child.size > 0:
            chars.append(child)
    lines: list[_CodeLine] = []
    for char in sorted(chars, key=lambda value: (-value.y0, value.x0)):
        if (
            not lines
            or abs(lines[-1].y - char.y0) > 0.25 * char.size
            or not 0.85 <= lines[-1].size / char.size <= 1.15
        ):
            lines.append(_CodeLine())
        lines[-1].chars.append(char)

    groups: list[list[_CodeLine]] = []
    for line in lines:
        if (
            not groups
            or groups[-1][-1].y - line.y > 4 * line.size
            or not 0.85 <= groups[-1][-1].size / line.size <= 1.15
        ):
            groups.append([])
        groups[-1].append(line)

    protected: set[LTChar] = set()
    for group in groups:
        evidence = [_statement_evidence(line.text) for line in group]
        statements = sum(statement for statement, _ in evidence)
        strong = sum(value for _, value in evidence)
        if statements < 3 or strong < 2:
            continue
        # Wrapped comments/calls need not be valid stand-alone Python, but an
        # occasional inline expression must not shelter a prose paragraph.
        non_comments = sum(not line.text.startswith("#") for line in group)
        if statements < 0.45 * non_comments:
            continue
        first = next(
            index for index, (statement, _) in enumerate(evidence) if statement
        )
        last = max(index for index, (statement, _) in enumerate(evidence) if statement)
        # A listing's introductory comments may wrap without repeating '#'.
        # Do not capture ordinary monospaced prose before those comments or
        # after the last statement merely because it uses the same font.
        first = next(
            (index for index in range(first) if group[index].text.startswith("#")),
            first,
        )
        while last + 1 < len(group) and re.match(
            r"(?:#|[)\]}]|(?:else|try|finally)\s*:)", group[last + 1].text
        ):
            last += 1
        protected.update(
            char for line in group[first : last + 1] for char in line.chars
        )
    return protected
