"""Code-aware tokenization for keyword search.

`getUserById`, `get_user_by_id` and "get user by id" should all match each other, so identifiers
are indexed both whole and split on camelCase / snake_case boundaries.
"""

import re

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "code", "do", "does", "for", "from",
    "how", "i", "in", "is", "it", "its", "me", "of", "on", "or", "show", "that", "the", "there",
    "this", "to", "what", "when", "where", "which", "who", "why", "with", "work", "works", "you",
    "implemented", "implement", "defined", "find", "explain", "used", "use", "uses",
}


def split_identifier(identifier: str) -> list[str]:
    """`HTTPServerError_v2` -> ["http", "server", "error", "v", "2"]."""
    parts: list[str] = []
    for piece in identifier.split("_"):
        parts.extend(p.lower() for p in _PARTS.findall(piece))
    return parts


def terms(text: str, min_len: int = 2) -> list[str]:
    """All searchable terms in `text`: each identifier lowercased plus its sub-words."""
    out: list[str] = []
    for ident in _IDENT.findall(text):
        whole = ident.lower().strip("_")
        if len(whole) >= min_len:
            out.append(whole)
        parts = split_identifier(ident)
        if len(parts) > 1:
            out.extend(p for p in parts if len(p) >= min_len)
    return out


def build_search_text(*texts: str) -> str:
    return " ".join(t for text in texts for t in terms(text))


def build_tsquery(query: str) -> str | None:
    """OR-query over the query's terms, with prefix matching on longer words."""
    seen: list[str] = []
    for t in terms(query):
        if t in STOPWORDS or t in seen:
            continue
        seen.append(t)
    if not seen:
        return None
    return " | ".join(f"{t}:*" if len(t) >= 4 else t for t in seen)
