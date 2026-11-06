"""Find HTTP endpoints declared in source code.

Covers FastAPI, Flask, Express, NestJS, Spring, and common Go routers. Extraction is pattern-based
and per file: routes are reported relative to the router they are declared on, and router mounts
(`include_router`, `app.use('/x', router)`, ...) are reported as MOUNT rows so prefixes can be
composed by reading them together.
"""

import re
from dataclasses import dataclass


@dataclass
class Endpoint:
    framework: str
    method: str
    route: str
    handler: str | None
    line: int  # 1-based


HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")


def _call_args(lines: list[str], line_idx: int, open_paren_col: int, max_lines: int = 12) -> str:
    """Text inside the parentheses that open at (line_idx, open_paren_col), across lines."""
    depth, out = 0, []
    for i in range(line_idx, min(len(lines), line_idx + max_lines)):
        segment = lines[i][open_paren_col:] if i == line_idx else lines[i]
        for ch in segment:
            if ch == "(":
                depth += 1
                if depth == 1:
                    continue
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return "".join(out)
            out.append(ch)
        out.append("\n")
    return "".join(out)


_STRING = re.compile(r"""[rfbu]?(['"`])((?:\\.|(?!\1).)*)\1""")


def _first_string(args: str) -> str | None:
    m = _STRING.search(args)
    return m.group(2) if m else None


def _keyword_string(args: str, *names: str) -> str | None:
    for name in names:
        m = re.search(rf"\b{name}\s*=\s*" + r"""[rf]?(['"])([^'"]*)\1""", args)
        if m:
            return m.group(2)
    return None


def _methods_list(args: str) -> list[str]:
    m = re.search(r"methods\s*=\s*[\[\(]([^\]\)]*)[\]\)]", args)
    if not m:
        return []
    return [s.upper() for s in re.findall(r"""['"](\w+)['"]""", m.group(1))]


# --- Python: FastAPI / Flask -------------------------------------------------------------------

_PY_DECORATOR = re.compile(r"^\s*@([A-Za-z_][\w.]*)\.(get|post|put|patch|delete|options|head|"
                           r"api_route|route|websocket)\s*\(")
_PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
_PY_ROUTER = re.compile(r"^\s*(\w+)\s*=\s*(APIRouter|Blueprint|FastAPI|Flask)\s*\(")
_PY_INCLUDE = re.compile(r"(\w+)\.(include_router|register_blueprint|mount)\s*\(")


def _python(lines: list[str]) -> list[Endpoint]:
    prefixes: dict[str, tuple[str, str]] = {}  # router var -> (prefix, framework)
    for i, line in enumerate(lines):
        m = _PY_ROUTER.match(line)
        if m:
            args = _call_args(lines, i, m.end() - 1)
            framework = "fastapi" if m.group(2) in {"APIRouter", "FastAPI"} else "flask"
            prefix = _keyword_string(args, "prefix", "url_prefix") or ""
            prefixes[m.group(1)] = (prefix, framework)

    found: list[Endpoint] = []
    for i, line in enumerate(lines):
        m = _PY_DECORATOR.match(line)
        if m:
            obj, verb = m.group(1), m.group(2)
            args = _call_args(lines, i, m.end() - 1)
            route = _first_string(args)
            if route is None:
                route = _keyword_string(args, "path", "rule") or ""
            prefix, framework = prefixes.get(obj.split(".")[-1], ("", None))
            if framework is None:
                framework = "flask" if verb == "route" else "fastapi"
            if verb in {"route", "api_route"}:
                methods = _methods_list(args) or ["GET"]
            elif verb == "websocket":
                methods = ["WS"]
            else:
                methods = [verb.upper()]
            handler = None
            for j in range(i + 1, min(len(lines), i + 30)):
                d = _PY_DEF.match(lines[j])
                if d:
                    handler = d.group(1)
                    break
            for method in methods:
                found.append(Endpoint(framework, method, prefix + route, handler, i + 1))
            continue
        inc = _PY_INCLUDE.search(line)
        if inc:
            args = _call_args(lines, i, inc.end() - 1)
            target = args.split(",")[0].strip()
            prefix = _keyword_string(args, "prefix", "url_prefix")
            if prefix is None:
                pm = re.search(r"prefix\s*=\s*([\w.]+)", args)
                prefix = "{" + pm.group(1) + "}" if pm else ""
            if inc.group(2) == "mount":
                prefix = _first_string(args) or prefix
                target = args.split(",")[1].strip() if "," in args else target
            framework = "flask" if inc.group(2) == "register_blueprint" else "fastapi"
            found.append(Endpoint(framework, "MOUNT", prefix, target, i + 1))
    return found


# --- JavaScript / TypeScript: Express, NestJS --------------------------------------------------

_JS_ROUTER_NAME = r"(?:app|server|router|api|routes?|\w*[Rr]outer|\w*[Aa]pp)"
_EXPRESS_ROUTE = re.compile(rf"\b({_JS_ROUTER_NAME})\.(get|post|put|patch|delete|all|options|head)\s*\(\s*"
                            r"""(['"`])(/[^'"`]*)\3""")
# `router.route("/x").get(...)`, often with `.route(` starting its own line.
_EXPRESS_CHAIN = re.compile(rf"(?:\b({_JS_ROUTER_NAME}))?\s*\.route\s*\(\s*" + r"""(['"`])(/[^'"`]*)\2\s*\)""")
_EXPRESS_USE = re.compile(rf"\b({_JS_ROUTER_NAME})\.use\s*\(\s*" + r"""(['"`])(/[^'"`]*)\2\s*,\s*([^)]+)\)""")
_NEST_CONTROLLER = re.compile(r"""@Controller\s*\(\s*(?:['"`]([^'"`]*)['"`])?""")
_NEST_ROUTE = re.compile(r"""@(Get|Post|Put|Patch|Delete|Options|Head|All)\s*\(\s*(?:['"`]([^'"`]*)['"`])?\s*\)""")
_JS_METHOD_NAME = re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?(\w+)\s*\(")


def _javascript(lines: list[str]) -> list[Endpoint]:
    found: list[Endpoint] = []
    controller_prefix = ""
    for i, line in enumerate(lines):
        for m in _EXPRESS_ROUTE.finditer(line):
            args = _call_args(lines, i, line.index("(", m.start()))
            parts = [p.strip() for p in args.split(",")]
            handler = parts[-1] if len(parts) > 1 and re.fullmatch(r"[\w.]+", parts[-1]) else "inline"
            found.append(Endpoint("express", m.group(2).upper(), m.group(4), handler, i + 1))
        for m in _EXPRESS_CHAIN.finditer(line):
            rest = line[m.end():] + "\n" + "\n".join(lines[i + 1:i + 15])
            tail = rest.split(";", 1)[0]
            for verb in re.findall(r"\.\s*(get|post|put|patch|delete|all)\s*\(", tail):
                found.append(Endpoint("express", verb.upper(), m.group(3), "inline", i + 1))
        for m in _EXPRESS_USE.finditer(line):
            found.append(Endpoint("express", "MOUNT", m.group(3), m.group(4).strip(), i + 1))
        c = _NEST_CONTROLLER.search(line)
        if c:
            controller_prefix = "/" + (c.group(1) or "").strip("/") if c.group(1) else ""
        r = _NEST_ROUTE.search(line)
        if r:
            handler = None
            for j in range(i + 1, min(len(lines), i + 10)):
                if lines[j].strip().startswith("@"):
                    continue
                hm = _JS_METHOD_NAME.match(lines[j])
                if hm:
                    handler = hm.group(1)
                    break
            sub = r.group(2) or ""
            route = controller_prefix + ("/" + sub.strip("/") if sub else "")
            found.append(Endpoint("nestjs", r.group(1).upper(), route or "/", handler, i + 1))
    return found


# --- Java: Spring ------------------------------------------------------------------------------

_SPRING_MAPPING = re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\b\s*(\()?")
_JAVA_TYPE_DECL = re.compile(r"\b(class|interface|record)\s+\w+")
_JAVA_METHOD = re.compile(r"(\w+)\s*\(")


def _spring_paths(args: str) -> list[str]:
    """`("/x")`, `(value = "/x")`, `(path = {"/a", "/b"})`, `({"/a", "/b"})` or `()` -> paths."""
    m = re.search(r"\b(?:value|path)\s*=\s*(\{[^}]*\}|\"[^\"]*\")", args)
    if m:
        target = m.group(1)
    else:
        positional = re.match(r'\s*(\{[^}]*\}|"[^"]*")', args)
        target = positional.group(1) if positional else ""
    return re.findall(r'"([^"]*)"', target) or [""]


def _java(lines: list[str]) -> list[Endpoint]:
    found: list[Endpoint] = []
    class_prefix = ""
    for i, line in enumerate(lines):
        m = _SPRING_MAPPING.search(line)
        if not m or line.strip().startswith(("//", "*", "/*")):  # skip mentions in comments and Javadoc
            continue
        args = _call_args(lines, i, m.end() - 1) if m.group(2) else ""
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith(("@", "//", "*", "/*"))
                                  or lines[j].strip().startswith(")")):
            j += 1
        declaration = lines[j] if j < len(lines) else ""
        if _JAVA_TYPE_DECL.search(declaration):
            class_prefix = _spring_paths(args)[0].rstrip("/")
            continue
        kind = m.group(1)
        if kind == "Request":
            methods = [x.upper() for x in re.findall(r"RequestMethod\.(\w+)", args)] or ["ANY"]
        else:
            methods = [kind.upper()]
        hm = _JAVA_METHOD.search(declaration)
        handler = hm.group(1) if hm else None
        for path in _spring_paths(args):
            route = class_prefix + (path if path.startswith("/") or not path else "/" + path)
            for method in methods:
                found.append(Endpoint("spring", method, route or "/", handler, i + 1))
    return found


# --- Go: net/http, gin, echo, chi ---------------------------------------------------------------

_GO_ROUTE = re.compile(r"\b(\w+)\.(GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete|HandleFunc|Handle)\s*\(\s*"
                       r'"([^"]+)"\s*,\s*([\w.]+)?')


def _go(lines: list[str]) -> list[Endpoint]:
    found: list[Endpoint] = []
    for i, line in enumerate(lines):
        for m in _GO_ROUTE.finditer(line):
            verb = m.group(2)
            route = m.group(3)
            method = "ANY" if verb in {"HandleFunc", "Handle"} else verb.upper()
            if method == "ANY" and " " in route:  # Go 1.22 patterns: "GET /items/{id}"
                method, route = route.split(" ", 1)
            if not route.startswith("/"):
                continue
            framework = "net/http" if verb in {"HandleFunc", "Handle"} else "go-router"
            found.append(Endpoint(framework, method, route, m.group(4), i + 1))
    return found


def extract_endpoints(language: str | None, text: str) -> list[Endpoint]:
    lines = text.split("\n")
    if language == "python":
        return _python(lines)
    if language in {"javascript", "typescript", "tsx"}:
        return _javascript(lines)
    if language == "java" or language == "kotlin":
        return _java(lines)
    if language == "go":
        return _go(lines)
    return []
