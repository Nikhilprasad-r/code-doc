"""Map file paths to a language name. Languages in TREE_SITTER_LANGS get syntax-aware chunking."""

from pathlib import PurePosixPath

TREE_SITTER_LANGS = {"python", "javascript", "typescript", "tsx", "java", "go"}

_EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    # Indexed as plain text (line-based chunks)
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".rb": "ruby",
    ".rs": "rust",
    ".cs": "csharp",
    ".php": "php",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "text",
    ".txt": "text",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".xml": "xml",
    ".properties": "properties",
    ".gradle": "gradle",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".vue": "vue",
    ".svelte": "svelte",
    ".graphql": "graphql",
    ".proto": "protobuf",
    ".env.example": "text",
    ".ini": "text",
    ".cfg": "text",
}

_FILENAMES = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "procfile": "text",
    "gemfile": "ruby",
    "go.mod": "text",
}

# Directories that never hold source worth indexing.
IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", "out", "target",
    ".next", ".nuxt", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".gradle", ".idea", ".vscode", "coverage", ".turbo", "bower_components",
}

# Generated or bulky files that add noise to retrieval.
IGNORED_FILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "Pipfile.lock",
    "Cargo.lock", "composer.lock", "go.sum", "Gemfile.lock",
}

IGNORED_SUFFIXES = (".min.js", ".min.css", ".map", ".snap")


def detect_language(path: str) -> str | None:
    p = PurePosixPath(path)
    name = p.name.lower()
    if name in _FILENAMES:
        return _FILENAMES[name]
    if name.startswith("dockerfile"):
        return "dockerfile"
    for ext, lang in _EXTENSIONS.items():
        if name.endswith(ext):
            return lang
    return None


def is_ignored_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in IGNORED_FILES or name.endswith(IGNORED_SUFFIXES)
