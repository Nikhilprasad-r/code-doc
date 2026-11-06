from app.retrieval.tokenize import build_tsquery, split_identifier, terms


def test_split_identifier_handles_camel_snake_and_acronyms():
    assert split_identifier("getUserById") == ["get", "user", "by", "id"]
    assert split_identifier("get_user_by_id") == ["get", "user", "by", "id"]
    assert split_identifier("HTTPServerError") == ["http", "server", "error"]


def test_terms_keep_whole_identifier_and_parts():
    found = terms("def getUserById(user_id): pass")
    assert "getuserbyid" in found
    assert {"user", "id", "by"} <= set(found)


def test_tsquery_drops_stopwords_and_prefixes_long_terms():
    query = build_tsquery("Where is getUserById implemented?")
    assert query is not None
    assert "getuserbyid:*" in query
    assert "where" not in query and "implemented" not in query


def test_tsquery_none_for_only_stopwords():
    assert build_tsquery("how does it work?") is None
