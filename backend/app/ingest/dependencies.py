"""Parse dependency manifests into (ecosystem, name, version, scope) rows."""

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass
class Dependency:
    ecosystem: str
    name: str
    version_spec: str | None
    scope: str | None


_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")


def _pep508(spec: str, scope: str | None) -> Dependency | None:
    m = _PEP508_NAME.match(spec)
    if not m:
        return None
    version = m.group(3).split(";")[0].strip() or None
    return Dependency("pypi", m.group(1), version, scope)


def _requirements(text: str, scope: str | None) -> list[Dependency]:
    deps = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        dep = _pep508(line, scope)
        if dep:
            deps.append(dep)
    return deps


def _pyproject(text: str) -> list[Dependency]:
    data = tomllib.loads(text)
    deps: list[Dependency] = []
    project = data.get("project", {})
    for spec in project.get("dependencies", []):
        if dep := _pep508(spec, "main"):
            deps.append(dep)
    for extra, specs in project.get("optional-dependencies", {}).items():
        for spec in specs:
            if dep := _pep508(spec, f"extra:{extra}"):
                deps.append(dep)
    for group, specs in data.get("dependency-groups", {}).items():
        for spec in specs:
            if isinstance(spec, str) and (dep := _pep508(spec, f"group:{group}")):
                deps.append(dep)
    for spec in data.get("tool", {}).get("uv", {}).get("dev-dependencies", []):
        if dep := _pep508(spec, "dev"):
            deps.append(dep)
    poetry = data.get("tool", {}).get("poetry", {})
    sections = [("main", poetry.get("dependencies", {})), ("dev", poetry.get("dev-dependencies", {}))]
    sections += [(f"group:{g}", v.get("dependencies", {})) for g, v in poetry.get("group", {}).items()]
    for scope, table in sections:
        for name, value in table.items():
            if name.lower() == "python":
                continue
            version = value if isinstance(value, str) else (value.get("version") if isinstance(value, dict) else None)
            deps.append(Dependency("pypi", name, version, scope))
    return deps


def _package_json(text: str) -> list[Dependency]:
    data = json.loads(text)
    deps = []
    for key, scope in [("dependencies", "main"), ("devDependencies", "dev"),
                       ("peerDependencies", "peer"), ("optionalDependencies", "optional")]:
        for name, version in (data.get(key) or {}).items():
            deps.append(Dependency("npm", name, str(version), scope))
    return deps


def _pom(text: str) -> list[Dependency]:
    root = ET.fromstring(text)
    deps = []

    def field(el: ET.Element, tag: str) -> str | None:
        child = el.find(f"{{*}}{tag}")
        if child is None:
            child = el.find(tag)
        return child.text.strip() if child is not None and child.text else None

    parent = root.find("{*}parent")
    if parent is not None:
        deps.append(Dependency("maven", f"{field(parent, 'groupId')}:{field(parent, 'artifactId')}",
                               field(parent, "version"), "parent"))
    for el in root.iter():
        if el.tag.split("}")[-1] != "dependency":
            continue
        group, artifact = field(el, "groupId"), field(el, "artifactId")
        if not artifact:
            continue
        deps.append(Dependency("maven", f"{group}:{artifact}", field(el, "version"), field(el, "scope") or "compile"))
    return deps


_GRADLE = re.compile(r"\b(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|"
                     r"annotationProcessor|developmentOnly|kapt|ksp)\s*\(?\s*['\"]([^'\"]+)['\"]")


def _gradle(text: str) -> list[Dependency]:
    deps = []
    for m in _GRADLE.finditer(text):
        parts = m.group(2).split(":")
        name = ":".join(parts[:2])
        version = parts[2] if len(parts) > 2 else None
        deps.append(Dependency("maven", name, version, m.group(1)))
    return deps


_GO_REQUIRE_LINE = re.compile(r"^\s*(?:require\s+)?([\w./~-]+\.[\w./~-]+)\s+(v[\w.+-]+)(\s*//\s*indirect)?", re.M)


def _go_mod(text: str) -> list[Dependency]:
    deps = []
    body = re.sub(r"^\s*(module|go|toolchain)\s+.*$", "", text, flags=re.M)
    for m in _GO_REQUIRE_LINE.finditer(body):
        deps.append(Dependency("go", m.group(1), m.group(2), "indirect" if m.group(3) else "direct"))
    return deps


def _cargo(text: str) -> list[Dependency]:
    data = tomllib.loads(text)
    deps = []
    for key, scope in [("dependencies", "main"), ("dev-dependencies", "dev"), ("build-dependencies", "build")]:
        for name, value in data.get(key, {}).items():
            version = value if isinstance(value, str) else value.get("version") if isinstance(value, dict) else None
            deps.append(Dependency("cargo", name, version, scope))
    return deps


def is_manifest(path: str) -> bool:
    name = PurePosixPath(path).name
    return (name in {"pyproject.toml", "package.json", "pom.xml", "go.mod", "Cargo.toml",
                     "build.gradle", "build.gradle.kts"}
            or (name.startswith("requirements") and name.endswith(".txt")))


def parse_manifest(path: str, text: str) -> list[Dependency]:
    """Parse a manifest; malformed files yield no rows rather than failing the whole index."""
    name = PurePosixPath(path).name
    try:
        if name == "pyproject.toml":
            return _pyproject(text)
        if name.startswith("requirements") and name.endswith(".txt"):
            scope = "dev" if "dev" in name or "test" in name else "main"
            return _requirements(text, scope)
        if name == "package.json":
            return _package_json(text)
        if name == "pom.xml":
            return _pom(text)
        if name in {"build.gradle", "build.gradle.kts"}:
            return _gradle(text)
        if name == "go.mod":
            return _go_mod(text)
        if name == "Cargo.toml":
            return _cargo(text)
    except (ValueError, ET.ParseError, tomllib.TOMLDecodeError, AttributeError):
        return []
    return []
