"""Extract symbol definitions (tree-sitter) and import statements (regex) from source files."""

import re
from dataclasses import dataclass
from functools import lru_cache

import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Tree

_GRAMMARS = {
    "python": tree_sitter_python.language,
    "javascript": tree_sitter_javascript.language,
    "typescript": tree_sitter_typescript.language_typescript,
    "tsx": tree_sitter_typescript.language_tsx,
    "java": tree_sitter_java.language,
    "go": tree_sitter_go.language,
}

_JS_DEFS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "method_definition": "method",
}
_TS_DEFS = {
    **_JS_DEFS,
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
DEFINITION_TYPES: dict[str, dict[str, str]] = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": _JS_DEFS,
    "typescript": _TS_DEFS,
    "tsx": _TS_DEFS,
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "record",
        "annotation_type_declaration": "annotation",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "go": {"function_declaration": "function", "method_declaration": "method", "type_spec": "type"},
}
_CONTAINER_KINDS = {"class", "interface", "enum", "record", "type"}

_DECORATIONS = {"decorator", "annotation", "marker_annotation"}
# What may sit between a decorator and its declaration: `@Controller("users") export class UsersController`.
_DECORATION_GAP = {"export", "default", "comment", "line_comment", "block_comment"}
SIGNATURE_MAX = 240
_SIGNATURE_END = re.compile(r"\s*(?:=>|[:{;=])\s*$")


@dataclass
class Symbol:
    name: str
    qualified: str
    kind: str
    start_line: int  # 1-based, inclusive
    end_line: int
    signature: str  # annotations, then the declaration up to its body, on one line


@lru_cache(maxsize=None)
def get_language(language: str) -> Language:
    return Language(_GRAMMARS[language]())


@lru_cache(maxsize=None)
def get_parser(language: str) -> Parser:
    return Parser(get_language(language))


def parse(language: str, source: bytes) -> Tree:
    return get_parser(language).parse(source)


def _name_of(node: Node) -> str | None:
    name = node.child_by_field_name("name")
    if name is not None and name.text:
        return name.text.decode("utf-8", "replace")
    return None


def _go_receiver_type(node: Node) -> str | None:
    receiver = node.child_by_field_name("receiver")
    if receiver is None or not receiver.text:
        return None
    match = re.search(r"\*?\s*(\w+)\s*(?:\[[^\]]*\])?\s*\)\s*$", receiver.text.decode("utf-8", "replace"))
    return match.group(1) if match else None


def _arrow_function_name(node: Node) -> str | None:
    """`const handler = async (req) => {...}` counts as a function named `handler`."""
    if node.type != "variable_declarator":
        return None
    value = node.child_by_field_name("value")
    if value is not None and value.type in {"arrow_function", "function_expression", "function"}:
        return _name_of(node)
    return None


def _squash(text: bytes) -> str:
    """Code spread over several lines, on one: `f(\\n    a,\\n    b,\\n)` -> `f(a, b)`."""
    line = " ".join(text.decode("utf-8", "replace").split())
    return re.sub(r",?\s+\)", ")", re.sub(r"\(\s+", "(", line))


def _decorations(node: Node) -> tuple[list[Node], list[Node]]:
    """A definition's annotations or decorators, split into those before the node and those inside it.
    Python and TypeScript put them before it (in `decorated_definition`, a class body or an `export`
    statement); Java puts them inside, in `modifiers`."""
    before: list[Node] = []
    sibling = node.prev_sibling
    while sibling is not None and (sibling.type in _DECORATIONS or sibling.type in _DECORATION_GAP):
        if sibling.type in _DECORATIONS:
            before.append(sibling)
        sibling = sibling.prev_sibling
    inside = [c for c in node.children if c.type in _DECORATIONS]
    for child in node.children:
        if child.type == "modifiers":
            inside.extend(c for c in child.children if c.type in _DECORATIONS)
    return before[::-1], sorted(inside, key=lambda n: n.start_byte)


def _body(node: Node) -> Node | None:
    body = node.child_by_field_name("body")
    if body is None and node.type == "variable_declarator":  # const handler = (req) => {...}
        value = node.child_by_field_name("value")
        body = value.child_by_field_name("body") if value is not None else None
    return body


def _signature(node: Node, source: bytes) -> str:
    """`@GetMapping("/owners") public String processFindForm(int page, Owner owner)`: the annotations,
    then the declaration up to its body, on one line. The declaration is kept whole (up to the limit);
    long annotations are shortened first."""
    before, inside = _decorations(node)
    body = _body(node)
    end = body.start_byte if body is not None else node.end_byte
    pieces: list[bytes] = []
    pos = node.start_byte
    for decoration in inside:
        if decoration.start_byte >= end:
            break
        pieces.append(source[pos:decoration.start_byte])
        pos = decoration.end_byte
    pieces.append(source[pos:end])
    head = _squash(b" ".join(pieces))
    if body is None:
        head = head.split("{", 1)[0].rstrip()  # Go `Server struct {...}` -> `Server struct`
    head = _SIGNATURE_END.sub("", head)[:SIGNATURE_MAX]

    tags = " ".join(_squash(source[d.start_byte:d.end_byte]) for d in [*before, *inside])
    room = SIGNATURE_MAX - len(head) - 1
    if len(tags) > room:
        tags = tags[:room - 1].rstrip() + "…" if room > 10 else ""
    return f"{tags} {head}" if tags else head


def extract_symbols(language: str, tree: Tree, source: bytes) -> list[Symbol]:
    def_types = DEFINITION_TYPES.get(language, {})
    symbols: list[Symbol] = []

    def visit(node: Node, scope: list[str], in_class: bool) -> None:
        kind = def_types.get(node.type)
        name = _name_of(node) if kind else None
        if kind is None and language in {"javascript", "typescript", "tsx"}:
            name = _arrow_function_name(node)
            kind = "function" if name else None

        child_scope, child_in_class = scope, in_class
        if kind and name:
            if kind == "function" and in_class:
                kind = "method"
            prefix = list(scope)
            if language == "go" and node.type == "method_declaration":
                receiver = _go_receiver_type(node)
                if receiver:
                    prefix = [receiver]
            qualified = ".".join([*prefix, name])
            start = node
            if node.parent is not None and node.parent.type == "decorated_definition":
                start = node.parent  # include Python decorators in the range
            symbols.append(
                Symbol(
                    name=name,
                    qualified=qualified,
                    kind=kind,
                    start_line=start.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    signature=_signature(node, source),
                )
            )
            child_scope = [*prefix, name]
            child_in_class = kind in _CONTAINER_KINDS
        for child in node.children:
            visit(child, child_scope, child_in_class)

    visit(tree.root_node, [], False)
    return symbols


_PY_FROM = re.compile(r"^\s*from\s+([.\w]+)\s+import\b", re.M)
_PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)", re.M)
_JS_IMPORT = re.compile(r"""\bimport\s+(?:[^'";]+?\s+from\s+)?['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)""")
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", re.M)
_GO_IMPORT_SINGLE = re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', re.M)
_GO_IMPORT_BLOCK = re.compile(r"^\s*import\s*\((.*?)\)", re.M | re.S)
_GO_QUOTED = re.compile(r'"([^"]+)"')


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_imports(language: str, text: str) -> list[tuple[str, int]]:
    """Return (module, line) pairs."""
    found: list[tuple[str, int]] = []
    if language == "python":
        for m in _PY_FROM.finditer(text):
            found.append((m.group(1), _line_of(text, m.start())))
        for m in _PY_IMPORT.finditer(text):
            line = _line_of(text, m.start())
            for part in m.group(1).split(","):
                found.append((part.strip().split(" as ")[0].strip(), line))
    elif language in {"javascript", "typescript", "tsx"}:
        for pattern in (_JS_IMPORT, _JS_REQUIRE):
            for m in pattern.finditer(text):
                found.append((m.group(1), _line_of(text, m.start())))
    elif language == "java":
        for m in _JAVA_IMPORT.finditer(text):
            found.append((m.group(1), _line_of(text, m.start())))
    elif language == "go":
        for m in _GO_IMPORT_SINGLE.finditer(text):
            found.append((m.group(1), _line_of(text, m.start())))
        for block in _GO_IMPORT_BLOCK.finditer(text):
            for q in _GO_QUOTED.finditer(block.group(1)):
                found.append((q.group(1), _line_of(text, block.start(1) + q.start())))
    return found
