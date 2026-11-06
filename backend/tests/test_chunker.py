from app.config import Settings
from app.ingest.pipeline import analyze_file


def _covered_lines(analysis) -> set[int]:
    return {n for c in analysis.chunks for n in range(c.start_line, c.end_line + 1)}


def test_chunks_cover_every_non_blank_line_without_overlap(analyzed):
    for path, analysis in analyzed.items():
        lines = analysis.text.split("\n")
        covered = _covered_lines(analysis)
        missing = [i + 1 for i, line in enumerate(lines) if line.strip() and i + 1 not in covered]
        assert not missing, f"{path}: lines {missing} not in any chunk"
        spans = sorted((c.start_line, c.end_line) for c in analysis.chunks)
        for (_, prev_end), (start, _) in zip(spans, spans[1:]):
            assert start > prev_end, f"{path}: overlapping chunks"


def test_chunk_content_matches_its_line_range(analyzed):
    for analysis in analyzed.values():
        lines = analysis.text.split("\n")
        for c in analysis.chunks:
            assert c.content == "\n".join(lines[c.start_line - 1:c.end_line])


def test_functions_are_not_split_when_they_fit(analyzed):
    items = analyzed["api/routes/items.py"]
    create = next(s for s in items.symbols if s.name == "create_item")
    holders = [c for c in items.chunks if c.start_line <= create.start_line and c.end_line >= create.end_line]
    assert holders, "create_item should sit inside a single chunk, decorator included"


def test_chunk_headers_carry_path_scope_and_definitions(analyzed):
    java = analyzed["svc/src/main/java/com/example/petclinic/OwnerController.java"]
    later = [c for c in java.chunks if "OwnerController.showOwner" in c.defines]
    assert later and later[0].scope == "class OwnerController"
    assert later[0].header.startswith("File: svc/src/main/java/com/example/petclinic/OwnerController.java")
    assert "Defines:" in later[0].header


def test_large_function_is_split_inside_its_body():
    body = "\n".join(f"    value_{i} = compute_something_long({i}, factor={i * 3})" for i in range(120))
    text = f"def huge():\n{body}\n    return value_0\n"
    analysis = analyze_file("big.py", text, "python", Settings().chunk_max_chars)
    assert len(analysis.chunks) > 1
    assert all(c.scope == "function huge" for c in analysis.chunks[1:])


def test_unparseable_language_falls_back_to_line_windows():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    analysis = analyze_file("notes.txt", text, "text", 500)
    assert len(analysis.chunks) > 1
    assert analysis.chunks[0].start_line == 1
    assert analysis.chunks[-1].end_line == 200
