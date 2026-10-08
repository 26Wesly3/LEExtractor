"""Offline UI tests (Streamlit AppTest).

The review page guards two rules that a user cannot see from the code:

* relevance ranking, title/abstract screening and full-text eligibility are
  three different things, and an uncalibrated score only ranks;
* every recorded decision stays reversible, with its audit trail.

Sessions are written under ``tests/_workspace_tmp/`` because the machine's
system temp directory is not writable inside the sandbox.
"""

import re
import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from litsearch.demo import demo_state
from litsearch.models import Paper
from litsearch.persistence import save_state
from litsearch.prisma import ScreeningDecision
from litsearch.version import version_tag

APP = str(Path(__file__).resolve().parents[1] / "app.py")
WORKSPACE_TMP = Path(__file__).resolve().parent / "_workspace_tmp"


@pytest.fixture
def app(request, monkeypatch):
    # One stable session directory per test, wiped first: the sandbox cannot
    # delete outside the workspace, and a leftover autosave would leak state
    # from the previous run into this one.
    session_dir = (WORKSPACE_TMP / "app_sessions"
                   / re.sub(r"\W+", "_", request.node.name)[:60])
    shutil.rmtree(session_dir, ignore_errors=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("litsearch.persistence.SESSION_DIR", str(session_dir))
    return AppTest.from_file(APP, default_timeout=20)


def _review_state(app):
    return app.session_state["review_state"]


def _record(state, paper):
    key = state.prisma._find_key(paper.id)
    return state.prisma.records[key]


def _restart(page="review", **session_state):
    """A fresh AppTest run that restores the autosaved session from disk.

    Streamlit's AppTest keeps the element tree of the run that called
    ``st.rerun()``, so a widget that disappears after a decision (the paper
    leaves the queue) leaves a node whose state is gone, and the *next*
    ``.run()`` raises. Reopening the app is what a user would do anyway, and it
    restores exactly the persisted session.
    """
    fresh = AppTest.from_file(APP, default_timeout=20)
    fresh.session_state["page"] = page
    fresh.session_state["review_mode"] = True
    for key, value in session_state.items():
        fresh.session_state[key] = value
    fresh.run()
    return fresh


def _texts(elements):
    return [element.value for element in elements]


def _labels(elements):
    return [element.label for element in elements]


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
    assert len(_review_state(app).prisma.get_included_papers()) == 1


def test_uncalibrated_threshold_slider_is_gone_and_screening_says_so(app):
    """v0.8.0 exposed a fixed 0.15 threshold that permanently rejected papers."""
    save_state(demo_state())
    app.session_state["page"] = "review"
    app.session_state["review_mode"] = True
    app.run()
    assert not app.exception
    assert [slider for slider in app.slider if slider.key == "screen_threshold"] == []
    assert not any("按标定阈值给出初筛建议" in button.label for button in app.button)
    assert any("尚未标定" in warning.value for warning in app.info)
    assert not app.session_state["review_state"].calibration


def test_calibration_record_is_stored_and_enables_assisted_screening(app):
    state = demo_state()
    save_state(state)
    app.session_state["page"] = "review"
    app.session_state["review_mode"] = True
    app.run()
    first, second = state.search_papers[:2]
    app.selectbox(key=f"cal_{first.canonical_id}").select("relevant")
    app.selectbox(key=f"cal_{second.canonical_id}").select("irrelevant").run()
    next(button for button in app.button if button.label == "计算并应用标定").click().run()
    assert not app.exception

    record = _review_state(app).calibration
    assert record is not None
    assert record.status == "calibrated"
    assert record.threshold is not None
    assert record.corpus_hash
    assert any("按标定阈值给出初筛建议" in button.label for button in app.button)

    next(button for button in app.button if button.label == "按标定阈值给出初筛建议").click().run()
    assert not app.exception
    state = _review_state(app)
    assert any(
        record.screening_decision is not ScreeningDecision.PENDING
        for record in state.prisma.records.values()
    )


def test_calibration_with_a_single_class_is_not_reusable(app):
    """Single-class labels must not be presented as a usable calibration."""
    state = demo_state()
    save_state(state)
    app.session_state["page"] = "review"
    app.session_state["review_mode"] = True
    app.run()
    first = state.search_papers[0]
    app.selectbox(key=f"cal_{first.canonical_id}").select("relevant").run()
    # One label is not enough to even submit the form.
    assert next(button for button in app.button if button.label == "计算并应用标定").disabled

    from litsearch.filters import CalibrationRecord, RelevanceFilter

    record = RelevanceFilter.build_calibration({"relevant": [first], "irrelevant": []})
    assert record.status == "insufficient_labels"
    assert record.threshold is None
    app.session_state["review_state"].calibration = CalibrationRecord(
        status="insufficient_labels", threshold=None
    )
    app.run()
    assert not app.exception
    assert not any("按标定阈值给出初筛建议" in button.label for button in app.button)
    assert any("不可用于自动筛选" in warning.value for warning in app.warning)


def test_review_page_separates_ranking_screening_and_full_text(app):
    save_state(demo_state())
    app.session_state["page"] = "review"
    app.session_state["review_mode"] = True
    app.run()
    assert not app.exception
    captions = " ".join(_texts(app.caption))
    assert "相关度排序" in captions and "标题/摘要筛选" in captions and "全文决定" in captions
    metric_labels = _labels(app.metric)
    for expected in ("筛选状态", "阈值已标定", "需人工复核", "全文评估完成"):
        assert expected in metric_labels
    assert "not_started" in _texts(app.metric)

    # The full-text stage offers no automatic threshold at all.
    next(radio for radio in app.radio if radio.label.startswith("评估阶段")).set_value("full").run()
    assert not app.exception
    assert any("全文纳入只能由人工明确评估" in warning.value for warning in app.warning)
    assert not any("阈值" in button.label for button in app.button)


def test_decisions_stay_reversible_with_an_audit_trail(app):
    state = demo_state()
    first = state.search_papers[0]
    save_state(state)
    app.session_state["page"] = "review"
    app.session_state["review_mode"] = True
    app.run()
    app.button(key=f"accept_title_abstract_{first.canonical_id}").click().run()
    assert not app.exception
    record = _record(_review_state(app), first)
    assert record.screening_decision is ScreeningDecision.ACCEPT
    assert record.history and record.history[-1]["after"] == "accept"
    assert record.history[-1]["reviewer"]

    reopened = _restart()
    next(button for button in reopened.button
         if button.key == f"undo_title_abstract_{first.canonical_id}").click().run()
    assert not reopened.exception
    record = _record(reopened.session_state["review_state"], first)
    assert record.screening_decision is ScreeningDecision.PENDING
    assert any(entry["before"] == "accept" and entry["after"] == "pending"
               for entry in record.history)
    assert "人工撤销" in record.history[-1]["reason"]


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
    assert not app.exception
    assert _record(_review_state(app), first).screening_decision is ScreeningDecision.ACCEPT

    full_text = _restart()
    next(radio for radio in full_text.radio if radio.label.startswith("评估阶段")).set_value("full").run()
    assert not full_text.exception
    assert full_text.button(key=f"accept_full_text_{first.canonical_id}").disabled
    full_text.checkbox(key=f"read_full_text_{first.canonical_id}").check().run()
    full_text.button(key=f"accept_full_text_{first.canonical_id}").click().run()
    assert not full_text.exception
    assert full_text.session_state["review_state"].prisma.full_text_stage_enabled


def test_result_filter_has_a_readable_empty_state(app):
    save_state(demo_state())
    app.session_state["page"] = "papers"
    app.run()
    app.text_input(key="result_text").input("there_is_no_such_paper").run()
    assert not app.exception
    assert any("没有符合条件" in info.value for info in app.info)


def test_papers_page_labels_the_slider_as_ranking_only(app):
    save_state(demo_state())
    app.session_state["page"] = "papers"
    app.run()
    assert not app.exception
    assert "仅排序" in app.slider(key="result_threshold").label
    captions = " ".join(_texts(app.caption))
    assert "不是纳入/排除决定" in captions


def test_export_page_exposes_screening_status_without_a_complete_review_claim(app):
    save_state(demo_state())
    app.session_state["page"] = "export"
    app.session_state["review_mode"] = True
    app.run()
    assert not app.exception
    metric_labels = _labels(app.metric)
    for expected in ("筛选状态", "阈值已标定", "需人工复核", "全文评估完成"):
        assert expected in metric_labels
    captions = " ".join(_texts(app.caption))
    assert "初筛结果，不是完成的系统综述" in captions
    assert not any("完整证据包" in button.label for button in app.button)
    assert not any("Impact Factor" in text or "影响因子" in text
                   for text in captions + " ".join(_texts(app.metric)))


def test_version_footer_reads_the_single_version_source(app):
    app.run()
    assert not app.exception
    assert version_tag() in _texts(app.sidebar.caption)


def test_offline_demo_load_populates_query_inputs_and_expands_without_api(app):
    app.run()
    next(button for button in app.button if button.label == "加载模拟演示").click().run()
    assert not app.exception
    assert app.text_input(key="topic_input").value == "deep learning plant phenotyping"
    app.radio(key="page").set_value("expand").run()
    next(button for button in app.button if button.label == "追溯引文").click().run()
    assert not app.exception
    assert app.session_state["review_state"].snowball_result.total_discovered == 3
