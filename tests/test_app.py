from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from litsearch.demo import demo_state
from litsearch.models import Paper
from litsearch.persistence import save_state
from litsearch.prisma import ScreeningDecision

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("litsearch.persistence.SESSION_DIR", str(tmp_path))
    return AppTest.from_file(APP, default_timeout=15)


def test_empty_app_runs_and_language_switches(app):
    app.run()
    assert not app.exception
    assert len(app.tabs) == 0
    app.selectbox(key="language").select("English").run()
    assert not app.exception
    assert app.header[0].value == "Start with a research question"


@pytest.mark.parametrize("page", ["explore", "papers", "expand", "landscape", "export", "review"])
def test_restored_state_renders_each_page_without_network(app, page):
    save_state(demo_state())
    app.session_state["page"] = page
    app.session_state["review_mode"] = True
    app.run()
    assert not app.exception
    assert app.session_state["review_state"].search_manifest["mode"] == "synthetic_demo"


def test_full_ids_prevent_screening_widget_collisions(app):
    state = demo_state()
    a = Paper(id="a" * 40 + "1", title="first paper")
    b = Paper(id="a" * 40 + "2", title="second paper")
    state.prisma.records.clear()
    state.prisma.add_papers([a, b])
    state.search_papers = [a, b]
    save_state(state)
    app.session_state["page"] = "review"
    app.session_state["review_mode"] = True
    app.run()
    assert not app.exception
    app.button(key=f"accept_title_abstract_{a.canonical_id}").click().run()
    assert not app.exception
    assert len(app.session_state["review_state"].prisma.get_included_papers()) == 1


def test_threshold_callback_runs_before_slider_and_updates_value(app):
    state = demo_state()
    save_state(state)
    app.session_state["page"] = "review"
    app.session_state["review_mode"] = True
    app.run()
    first, second = state.search_papers[:2]
    app.selectbox(key=f"cal_{first.canonical_id}").select("relevant")
    app.selectbox(key=f"cal_{second.canonical_id}").select("irrelevant").run()
    button = next(b for b in app.button if b.label == "计算并应用阈值")
    button.click().run()
    assert not app.exception
    assert app.slider(key="screen_threshold").value != 0.15


def test_maybe_remains_actionable_and_full_text_accept_needs_reading(app):
    state = demo_state()
    first = state.search_papers[0]
    state.prisma.screen_paper(first.id, ScreeningDecision.MAYBE)
    save_state(state)
    app.session_state["page"] = "review"
    app.session_state["review_mode"] = True
    app.run()
    assert not app.exception
    app.button(key=f"accept_title_abstract_{first.canonical_id}").click().run()
    next(r for r in app.radio if r.label == "评估阶段").set_value("full").run()
    assert not app.exception
    assert app.button(key=f"accept_full_text_{first.canonical_id}").disabled
    app.checkbox(key=f"read_full_text_{first.canonical_id}").check().run()
    app.button(key=f"accept_full_text_{first.canonical_id}").click().run()
    assert not app.exception
    assert app.session_state["review_state"].prisma.full_text_stage_enabled


def test_result_filter_has_a_readable_empty_state(app):
    save_state(demo_state())
    app.session_state["page"] = "papers"
    app.run()
    app.text_input(key="result_text").input("there_is_no_such_paper").run()
    assert not app.exception
    assert any("没有符合条件" in info.value for info in app.info)


def test_offline_demo_load_populates_query_inputs_and_expands_without_api(app):
    app.run()
    next(b for b in app.button if b.label == "加载模拟演示").click().run()
    assert not app.exception
    assert app.text_input(key="topic_input").value == "deep learning plant phenotyping"
    app.radio(key="page").set_value("expand").run()
    next(b for b in app.button if b.label == "追溯引文").click().run()
    assert not app.exception
    assert app.session_state["review_state"].snowball_result.total_discovered == 3
