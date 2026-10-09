from bench.actions import BY_NAME, DESTRUCTIVE
from bench.catalog import Case, load_catalog, norm, score


def _case(action: str, params: dict | None = None) -> Case:
    return Case(id="T", group="test", text="t", action=action, params=params or {},
                tags=frozenset())


def test_catalog_ids_are_unique():
    ids = [c.id for c in load_catalog()]
    assert len(ids) == len(set(ids))


def test_catalog_uses_only_known_actions_and_params():
    for case in load_catalog():
        if case.skip_llm:
            continue
        assert case.action in BY_NAME, case.id
        allowed = {p.name for p in BY_NAME[case.action].params}
        assert set(case.params) <= allowed, (case.id, set(case.params) - allowed)


def test_destructive_tag_matches_action_tier():
    for case in load_catalog():
        assert ("destructive" in case.tags) == (case.action in DESTRUCTIVE), case.id


def test_norm_ignores_case_slashes_and_quotes():
    assert norm(' "Documents\\Notes.TXT" ') == "documents/notes.txt"
    assert norm("Downloads/") == "downloads"
    assert norm("") is None
    assert norm(None) is None


def test_score_accepts_any_listed_value():
    case = _case("app.open", {"app": ("chrome", "google chrome")})
    assert score(case, {"action": "app.open", "params": {"app": "Google Chrome"}}).params_ok


def test_score_wrong_param_fails_params_but_not_action():
    case = _case("app.open", {"app": ("chrome",)})
    s = score(case, {"action": "app.open", "params": {"app": "firefox"}})
    assert s.action_ok and not s.params_ok


def test_score_missing_param_must_be_null():
    case = _case("file.delete", {"path": None})
    assert score(case, {"action": "file.delete", "params": {"path": None}}).params_ok
    assert not score(case, {"action": "file.delete", "params": {"path": "x.txt"}}).params_ok


def test_destructive_false_positive_is_flagged():
    s = score(_case("qa.answer"), {"action": "file.delete", "params": {"path": None}})
    assert s.destructive_false_positive and not s.action_ok


def test_destructive_miss_is_flagged():
    s = score(_case("power.shutdown"), {"action": "qa.answer", "params": {}})
    assert s.destructive_miss and not s.destructive_false_positive


def test_unparseable_output_scores_as_wrong():
    s = score(_case("system.cpu"), None)
    assert not s.action_ok and not s.params_ok
