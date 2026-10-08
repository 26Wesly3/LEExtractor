"""LEExtractor — Systematic Literature Review Toolkit (Streamlit GUI).

4-Phase gold-standard workflow:
1. Scoping — field landscape exploration
2. Systematic Search — multi-database keyword search
3. Snowballing — citation tracing + similar paper discovery
4. PRISMA — two-stage screening (title/abstract → full text) + flow diagram + PDF

Progress is autosaved to `.sessions/` after every action, so a browser
refresh no longer throws away a long run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import matplotlib.pyplot as plt
import streamlit as st

st.set_page_config(
    page_title="LEExtractor — Literature Review Toolkit",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

from litsearch.config import s2_key_configured, set_env_value
from litsearch.diagnostics import SourceErrorKind, get_diagnostics, reset_diagnostics
from litsearch.filters import RelevanceFilter
from litsearch.persistence import (
    delete_session,
    list_sessions,
    load_state,
    save_state,
    session_age_text,
)
from litsearch.prisma import (
    FULL_TEXT_EXCLUSION_REASONS,
    ScreeningDecision,
    ScreeningStage,
)
from litsearch.search import LiteratureReviewWorkflow, ReviewPhase
from litsearch.sources import ImpactFactorLookup, reset_s2_stats, s2_stats


def persist(state) -> None:
    """Autosave the current review state (cheap: a single JSON file)."""
    try:
        save_state(state)
    except Exception as e:  # never let a save failure break the UI
        st.warning(f"自动保存失败 / autosave failed: {e}")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def render_sidebar():
    st.sidebar.title("📚 LEExtractor")
    st.sidebar.caption("Systematic Literature Review Toolkit")
    st.sidebar.caption("系统文献综述工具 · PRISMA 2020 · TARCiS")

    st.sidebar.divider()
    st.sidebar.subheader("Global Settings / 全局设置")

    years_back = st.sidebar.slider("Years Back / 回溯年数", 5, 50, 20)
    st.sidebar.divider()
    max_papers = st.sidebar.slider("Max Search Papers / 最大检索论文数", 50, 500, 200, 50)

    # --- Semantic Scholar key -------------------------------------------
    st.sidebar.divider()
    st.sidebar.subheader("Semantic Scholar API Key")
    if s2_key_configured():
        st.sidebar.success("✅ 已配置 / key configured")
    else:
        st.sidebar.warning(
            "⚠️ 未配置：检索会频繁被限流（429），结果偏少。\n\n"
            "免费申请：semanticscholar.org/product/api#api-key-form"
        )
    with st.sidebar.expander("设置密钥 / set key"):
        key_input = st.text_input("S2_API_KEY", type="password", key="s2_key_input")
        if st.button("保存到 .env / save", key="save_s2_key") and key_input.strip():
            try:
                set_env_value("S2_API_KEY", key_input.strip())
                st.success("已保存，下次检索生效 / saved, effective next search")
            except Exception as e:
                st.error(f"保存失败 / failed: {e}")

    # --- Sessions --------------------------------------------------------
    st.sidebar.divider()
    st.sidebar.subheader("Session / 进度")
    sessions = list_sessions()
    if sessions:
        latest = sessions[0]
        st.sidebar.caption(
            f"自动保存 / autosaved: **{latest['topic'] or '—'}** "
            f"({session_age_text(latest['saved_at'])})"
        )
        if st.sidebar.button("🗑 丢弃进度 / discard", use_container_width=True):
            for s in sessions:
                delete_session(s["path"])
            st.session_state.pop("review_state", None)
            st.rerun()
    else:
        st.sidebar.caption("暂无保存的进度 / no saved session")

    st.sidebar.divider()
    st.sidebar.caption(
        "Sources / 数据源: Semantic Scholar · OpenAlex · arXiv · Crossref\n"
        "Download / 下载: arXiv · OpenAccess (OpenAlex/Unpaywall)"
    )
    st.sidebar.caption("v0.4.0 — PRISMA two-stage + autosave")

    return years_back, max_papers


# ---------------------------------------------------------------------------
# Phase indicator
# ---------------------------------------------------------------------------


PHASE_LABELS = {
    ReviewPhase.SCOPING: "1. Scoping / 领域概览",
    ReviewPhase.SYSTEMATIC_SEARCH: "2. Systematic Search / 系统检索",
    ReviewPhase.SNOWBALLING: "3. Snowballing / 引文追溯",
    ReviewPhase.SCREENING: "4. Screening & PRISMA / 筛选与报告",
    ReviewPhase.COMPLETE: "✓ Complete / 完成",
}


def render_phase_indicator(phase):
    phase_order = {
        ReviewPhase.SCOPING: 0,
        ReviewPhase.SYSTEMATIC_SEARCH: 1,
        ReviewPhase.SNOWBALLING: 2,
        ReviewPhase.SCREENING: 3,
        ReviewPhase.COMPLETE: 4,
    }
    cols = st.columns(4)
    phases = [
        (ReviewPhase.SCOPING, "1", "Scoping"),
        (ReviewPhase.SYSTEMATIC_SEARCH, "2", "Search"),
        (ReviewPhase.SNOWBALLING, "3", "Snowball"),
        (ReviewPhase.SCREENING, "4", "PRISMA"),
    ]
    current_idx = phase_order.get(phase, 0)
    for i, (ph, num, label) in enumerate(phases):
        with cols[i]:
            this_idx = phase_order.get(ph, 0)
            if current_idx == this_idx:
                st.markdown(f"**🔵 {num}. {label}**")
            elif current_idx > this_idx:
                st.markdown(f"✅ {num}. {label}")
            else:
                st.markdown(f"⚪ {num}. {label}")


def show_rate_limit_warning():
    stats = s2_stats()
    if stats["rate_limited"]:
        st.warning(
            f"⚠️ Semantic Scholar 拒绝了 {stats['rate_limited']} 次请求（HTTP 429）。"
            "本次检索结果可能不完整 —— 配一个 S2_API_KEY（左侧栏）会好很多。"
        )
        reset_s2_stats()
    show_diagnostics_panel()


def show_diagnostics_panel():
    """Surface *why* results may be incomplete, not just that they might be.

    The previous behaviour logged everything at `warning` and swallowed it,
    so a malformed query and a dropped connection were indistinguishable from
    the outside. Anything that needs a human is now visible here.
    """
    diag = get_diagnostics()
    if not diag.events:
        return

    counts = diag.counts()
    n_perm = sum(
        counts.get(k.value, 0)
        for k in (
            SourceErrorKind.CONTRACT,
            SourceErrorKind.PARSE,
        )
    )

    if n_perm:
        st.error(
            f"🔴 **{n_perm} 个请求因参数或契约问题失败** —— 这些重试也没用，"
            "说明检索式或字段用法有问题，结果可能缺失文献。"
            f"展开下方「数据源诊断」看具体是哪几个。"
        )
    elif diag.events:
        st.warning(f"ℹ️ {diag.summary()}")

    with st.expander(f"🔍 数据源诊断 / Source diagnostics ({len(diag.events)})"):
        by_kind: dict[str, list] = {}
        for e in diag.events:
            if "truncated" in e.message:
                continue
            by_kind.setdefault(e.kind.value, []).append(e)

        for kind, events in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
            icon = {
                "rate_limited": "🐢 限流",
                "contract": "🚫 参数错误",
                "parse": "🧩 解析失败",
                "not_found": "🔍 未找到",
                "transient": "🌐 网络问题",
            }.get(kind, kind)
            st.markdown(f"**{icon} · {len(events)} 次**")
            # Show more where it matters: failures that need a human decision
            # are worth the screen space, transient blurs are not.
            limit = 8 if kind in ("contract", "parse") else 3
            for e in events[:limit]:
                st.text(f"  {e.short(150)}")
            if len(events) > limit:
                st.caption(f"  …另有 {len(events) - limit} 条同类记录")

        if diag.has_contract_problems():
            st.caption(
                "「参数错误 / 解析失败」这一类通常意味着字段名或参数格式与 API 契约不符，"
                "值得对照官方文档检查一眼。"
            )


# ---------------------------------------------------------------------------
# Phase 1: Scoping
# ---------------------------------------------------------------------------


def render_scoping(workflow, years_back):
    st.subheader("Phase 1: Scoping / 领域概览")
    st.caption("Explore the field landscape before committing to a full search.")

    col1, col2 = st.columns(2)
    with col1:
        topic = st.text_input(
            "Research Topic / 研究课题",
            value=st.session_state.get("topic", ""),
            placeholder="e.g. mint essential oil biosynthesis, plant phenotyping deep learning...",
            key="scoping_topic",
        )
    with col2:
        direction = st.text_input(
            "Research Direction / 研究方向 (for relevance scoring)",
            value=st.session_state.get("direction", ""),
            placeholder="Describe your focus, e.g. terpenoid biosynthesis pathway...",
            key="scoping_direction",
        )

    if st.button("🔍 Start Scoping / 开始概览", type="primary", use_container_width=True, disabled=not topic.strip()):
        with st.spinner("Searching and analyzing field landscape..."):
            state = workflow.scope_topic(
                topic=topic.strip(),
                research_direction=direction.strip(),
                years_back=years_back,
                initial_limit=50,
            )
            st.session_state["review_state"] = state
            st.session_state["topic"] = topic.strip()
            st.session_state["direction"] = direction.strip()
            persist(state)
            st.rerun()

    state = st.session_state.get("review_state")
    if state is None or state.phase != ReviewPhase.SCOPING:
        return

    st.divider()

    if not state.scoping_papers:
        st.info("No scoping results yet. Enter a topic and click 'Start Scoping'.")
        return

    cols = st.columns(4)
    cols[0].metric("Papers Found / 检索到文献", len(state.scoping_papers))
    cols[1].metric("Key Journals / 主要期刊", len(state.key_journals))
    cols[2].metric("Key Authors / 主要作者", len(state.key_authors))
    cols[3].metric("Topic Clusters / 主题簇", len(state.topic_clusters))

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Topic Clusters / 主题簇")
        for cluster_name, keywords in list(state.topic_clusters.items())[:6]:
            st.markdown(f"**{cluster_name}**: {', '.join(keywords)}")

    with col2:
        if state.year_distribution:
            st.subheader("Publication Timeline / 发表时间分布")
            fig, ax = plt.subplots(figsize=(5, 2.5))
            years = list(state.year_distribution.keys())
            counts = list(state.year_distribution.values())
            ax.bar(years, counts, color="#3498db", alpha=0.8)
            ax.set_xlabel("Year")
            ax.set_ylabel("Count")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            fig.tight_layout()
            st.pyplot(fig)
            plt.close(fig)

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Key Journals / 主要期刊")
        for journal, count in state.key_journals[:10]:
            st.markdown(f"- {journal} ({count})")
    with col2:
        st.subheader("Key Authors / 主要作者")
        for author, count in state.key_authors[:10]:
            st.markdown(f"- {author} ({count})")

    st.divider()
    st.info("👉 转到 **系统检索** 标签页（第2个标签）执行多数据库检索。")


# ---------------------------------------------------------------------------
# Phase 2: Systematic Search
# ---------------------------------------------------------------------------


def render_systematic_search(workflow, max_papers, years_back):
    st.subheader("Phase 2: Systematic Search / 系统检索")
    st.caption("Multi-database search: S2 + OpenAlex + arXiv + Crossref")

    state = st.session_state.get("review_state")

    if state is None:
        st.warning("Complete scoping first (Tab 1).")
        return

    col1, col2 = st.columns(2)
    with col1:
        min_cit = st.number_input("Min Citations / 最低引用数", 0, 1000, 0, 10, key="sys_min_cit")
    with col2:
        st.caption("")

    search_not_done = state.phase.value in {ReviewPhase.SCOPING.value}

    if search_not_done:
        if st.button("🔍 Run Systematic Search / 执行系统检索", type="primary", use_container_width=True):
            reset_diagnostics()  # diagnostics describe one run, not all time
            with st.spinner(f"Searching across all databases for '{state.topic}'..."):
                state = workflow.systematic_search(
                    state, max_papers=max_papers, years_back=years_back, min_citations=min_cit,
                )
                st.session_state["review_state"] = state
                persist(state)
                st.rerun()
        st.info("Configure search parameters above, then click the button.")
        return

    st.divider()

    papers = state.search_papers
    if not papers:
        st.warning("Search returned no results. Try broader keywords or increase years back.")
        show_rate_limit_warning()
        if st.button("🔄 Retry Search / 重新检索", use_container_width=True):
            state = workflow.systematic_search(
                state, max_papers=max_papers, years_back=years_back, min_citations=min_cit,
            )
            st.session_state["review_state"] = state
            persist(state)
            st.rerun()
        return

    show_rate_limit_warning()

    cols = st.columns(3)
    cols[0].metric("Total Found / 检索结果", len(papers))
    cols[1].metric(
        "Avg Relevance / 平均相关度",
        f"{sum(p.relevance_score for p in papers) / max(len(papers), 1):.3f}",
    )
    cols[2].metric(
        "Avg Citations / 平均引用",
        f"{sum(p.citation_count for p in papers) / max(len(papers), 1):.0f}",
    )

    st.divider()

    st.subheader("Top 20 Papers / 前20篇文献")
    if "if_lookup" not in st.session_state:
        st.session_state["if_lookup"] = ImpactFactorLookup()
    if_lookup = st.session_state["if_lookup"]

    for idx, paper in enumerate(papers[:20]):
        with st.container():
            col1, col2 = st.columns([6, 1])
            with col1:
                venue_str = f" | {paper.venue}" if paper.venue else ""
                if_val = if_lookup.get_if_display(paper.venue or "")
                if "N/A" not in if_val:
                    venue_str += f" | {if_val}"
                preprint_tag = " [Preprint]" if paper.source == "arxiv" else ""

                st.markdown(
                    f"**{idx + 1}.** *{paper.title[:200]}*  "
                    f"({paper.year or '?'}){preprint_tag}"
                )
                st.caption(
                    f"👥 {', '.join(a.name for a in paper.authors[:3])}  |  "
                    f"📖 {paper.citation_count} citations  |  "
                    f"🎯 Relevance: {paper.relevance_score:.3f}"
                    f"{venue_str}"
                )
            with col2:
                if paper.url and paper.url.startswith("http"):
                    st.link_button("📥 PDF", paper.url, use_container_width=True)
                if paper.doi:
                    st.link_button("🔗 DOI", f"https://doi.org/{paper.doi}", use_container_width=True)

            if paper.abstract:
                with st.expander("Abstract / 摘要"):
                    st.write(paper.abstract[:500])

            st.divider()

    st.divider()
    st.info("👉 转到 **引文追溯** 标签页（第3个标签）执行引文追溯和相似文献发现。")


# ---------------------------------------------------------------------------
# Phase 3: Snowballing
# ---------------------------------------------------------------------------


def render_snowballing(workflow):
    st.subheader("Phase 3: Snowballing + Similar Papers / 引文追溯与相似文献")
    st.caption(
        "TARCiS-aligned citation tracing: forward (cited-by) + backward (references). "
        "Plus bibliographic coupling and co-citation analysis."
    )

    state = st.session_state.get("review_state")
    if state is None or not state.search_papers:
        st.warning("Complete systematic search first.")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        num_seeds = st.number_input("Seed Papers / 种子论文数", 3, 30, 10)
    with col2:
        max_rounds = st.number_input("Snowball Rounds / 追溯轮次", 1, 5, 3)
    with col3:
        max_per_dir = st.number_input("Max per Direction / 每方向最多", 10, 100, 50)

    st.caption(
        f"预计请求数 ≈ {num_seeds * 2 * max_rounds} 次；"
        f"没配 S2_API_KEY 时会自动降速，可能要几分钟；每轮结束自动保存进度。"
    )

    btn_col1, btn_col2 = st.columns(2)

    with btn_col1:
        if st.button("🔄 Run Snowballing / 执行引文追溯", type="primary", use_container_width=True):
            reset_diagnostics()
            progress = st.progress(0)
            status = st.empty()
            state = st.session_state["review_state"]

            def on_round(round_num, total, found):
                progress.progress(min(1.0, round_num / max(total, 1)))
                status.text(f"Round {round_num}/{total} — 新发现 {found} 篇")
                persist(state)

            with st.spinner("Running iterative forward+backward snowballing..."):
                state = workflow.run_snowballing(
                    state,
                    num_seeds=num_seeds,
                    max_rounds=max_rounds,
                    max_per_direction=max_per_dir,
                    on_round=on_round,
                )
                st.session_state["review_state"] = state
                persist(state)
                st.rerun()

    with btn_col2:
        if st.button("🔗 Find Similar Papers / 查找相似文献", type="secondary", use_container_width=True):
            with st.spinner("Finding similar papers via bibliographic coupling + co-citation..."):
                state = workflow.find_similar(state, num_seeds=num_seeds)
                st.session_state["review_state"] = state
                persist(state)
                st.rerun()

    show_rate_limit_warning()

    sn_result = state.snowball_result
    if sn_result and sn_result.rounds:
        st.divider()
        st.subheader("Snowballing Results / 引文追溯结果")

        cols = st.columns(3)
        cols[0].metric("Total Rounds / 追溯轮次", len(sn_result.rounds))
        cols[1].metric("Papers Discovered / 新发现文献", sn_result.total_discovered)
        cols[2].metric("Saturated / 是否饱和", "Yes" if sn_result.saturated else "No")

        for rnd in sn_result.rounds:
            with st.expander(f"Round {rnd.round_number}: {rnd.count} new papers / {rnd.count} 篇新文献"):
                for p in rnd.new_papers[:10]:
                    st.markdown(
                        f"- **{p.title[:120]}** ({p.year or '?'}) — "
                        f"Cited {p.citation_count}x | Rel: {p.relevance_score:.3f}"
                    )

    similar = state.similar_papers
    if similar:
        st.divider()
        st.subheader(f"Similar Papers / 相似文献 ({len(similar)} found)")
        for paper, score, method in similar[:15]:
            method_label = "BC" if method == "bibliographic_coupling" else "CC"
            st.markdown(
                f"- **{paper.title[:120]}** ({paper.year or '?'}) — "
                f"Score: {score:.2f} ({method_label})"
            )

    if state.phase in {ReviewPhase.SNOWBALLING, ReviewPhase.SCREENING, ReviewPhase.COMPLETE}:
        st.divider()
        st.info("👉 转到 **PRISMA** 标签页（第4个标签）做两阶段筛选并生成流程图。")


# ---------------------------------------------------------------------------
# Phase 4: PRISMA + two-stage screening
# ---------------------------------------------------------------------------


def render_prisma(workflow):
    st.subheader("Phase 4: Screening & PRISMA / 两阶段筛选与PRISMA报告")
    st.caption(
        "Stage 1 标题/摘要筛选 → Stage 2 全文评估（PRISMA 2020 要求两阶段分开计数）"
    )

    state = st.session_state.get("review_state")
    if state is None:
        st.warning("Complete earlier phases first.")
        return

    report = state.prisma.generate_report()

    cols = st.columns(5)
    cols[0].metric("Total Tracked / 追踪文献", report.records_after_dedup)
    cols[1].metric("Screened / 已初筛", report.records_screened)
    cols[2].metric("Excluded (T/A) / 初筛排除", report.records_excluded_title_abstract)
    cols[3].metric("Reports sought / 待取全文", report.reports_sought)
    cols[4].metric("Included / 最终纳入", report.studies_included)

    st.divider()

    stage_label = st.radio(
        "Screening Stage / 筛选阶段",
        ["1. 标题/摘要筛选 (title/abstract)", "2. 全文评估 (full text)"],
        horizontal=True,
        key="prisma_stage",
    )
    stage = (
        ScreeningStage.FULL_TEXT
        if stage_label.startswith("2")
        else ScreeningStage.TITLE_ABSTRACT
    )

    # ---- Stage 1 -------------------------------------------------------
    if stage == ScreeningStage.TITLE_ABSTRACT:
        col1, col2 = st.columns(2)
        with col1:
            threshold = st.slider(
                "Auto-Screen Threshold / 自动筛选阈值", 0.0, 1.0,
                st.session_state.get("screen_threshold", 0.15), 0.01,
                help="Papers with relevance >= threshold are auto-accepted, below are rejected.",
            )
            st.session_state["screen_threshold"] = threshold
        with col2:
            st.caption("")
            if st.button("⚡ Auto-Screen / 自动初筛", type="primary", use_container_width=True):
                with st.spinner("Auto-screening papers by relevance..."):
                    state = workflow.auto_screen(
                        state, relevance_threshold=threshold, stage=ScreeningStage.TITLE_ABSTRACT
                    )
                    st.session_state["review_state"] = state
                    persist(state)
                    st.rerun()

        render_threshold_calibrator(workflow, state, threshold)
        queue = workflow.get_screening_queue(state, ScreeningStage.TITLE_ABSTRACT)
        render_manual_queue(workflow, state, queue, ScreeningStage.TITLE_ABSTRACT)

    # ---- Stage 2 -------------------------------------------------------
    else:
        queue = workflow.get_screening_queue(state, ScreeningStage.FULL_TEXT)
        st.caption(
            f"进入全文评估的文献：{report.reports_sought} 篇；"
            f"其中 {report.reports_not_retrieved} 篇尚未标记取到全文。"
        )
        if not queue:
            st.success("全文评估已完成 / full-text assessment complete")
        for record in queue[:20]:
            paper = record.paper
            with st.container():
                col1, col2 = st.columns([5, 3])
                with col1:
                    st.markdown(f"**{paper.title[:150]}** ({paper.year or '?'})")
                    st.caption(
                        f"Source: {record.source} | Citations: {paper.citation_count} | "
                        f"Relevance: {record.relevance_score:.3f}"
                    )
                with col2:
                    got = st.checkbox(
                        "全文已获取 / retrieved",
                        value=record.full_text_retrieved,
                        key=f"ft_got_{paper.id[:30]}",
                    )
                    if got != record.full_text_retrieved:
                        workflow.mark_full_text_retrieved(state, paper.id, got)
                        st.session_state["review_state"] = state
                        persist(state)
                        st.rerun()

                if record.full_text_retrieved:
                    reason = st.selectbox(
                        "排除原因 / exclusion reason (only used when rejecting)",
                        FULL_TEXT_EXCLUSION_REASONS,
                        key=f"ft_reason_{paper.id[:30]}",
                    )
                    b1, b2, b3 = st.columns([1, 1, 4])
                    with b1:
                        if st.button("✅ 纳入", key=f"ft_acc_{paper.id[:30]}", use_container_width=True):
                            workflow.screen_paper(
                                state, paper.id, ScreeningDecision.ACCEPT,
                                stage=ScreeningStage.FULL_TEXT,
                            )
                            st.session_state["review_state"] = state
                            persist(state)
                            st.rerun()
                    with b2:
                        if st.button("❌ 排除", key=f"ft_rej_{paper.id[:30]}", use_container_width=True):
                            workflow.screen_paper(
                                state, paper.id, ScreeningDecision.REJECT, reason=reason,
                                stage=ScreeningStage.FULL_TEXT,
                            )
                            st.session_state["review_state"] = state
                            persist(state)
                            st.rerun()

    st.divider()

    if st.button("📊 Generate PRISMA Report / 生成PRISMA流程图", type="primary", use_container_width=True):
        report = workflow.generate_prisma_report(state)
        st.session_state["review_state"] = state
        persist(state)
        st.rerun()

    if state.phase == ReviewPhase.COMPLETE or report.studies_included > 0:
        st.divider()
        st.subheader("PRISMA 2020 Flow Diagram / PRISMA流程图")
        report = state.prisma.generate_report()
        st.markdown(report.to_mermaid())
        with st.expander("Flow Data / 流程数据"):
            st.json(report.to_flow_dict())

    included = workflow.get_included_papers(state)

    if included:
        st.divider()
        st.subheader("📦 Batch Download / 批量下载")
        st.info(
            f"{len(included)} papers ready for download. "
            f"PDFs saved to: `{workflow.downloader.get_download_dir()}`"
        )
        if st.button("📥 Download All Included PDFs / 下载全部纳入文献", type="primary", use_container_width=True):
            progress = st.progress(0)
            status_text = st.empty()
            results = []
            for i, paper in enumerate(included):
                status_text.text(f"Downloading ({i + 1}/{len(included)}): {paper.title[:80]}...")
                path = workflow.downloader.download_pdf(paper)
                results.append((paper, path))
                progress.progress((i + 1) / len(included))
            success = [r for r in results if r[1] is not None]
            status_text.text(f"Done: {len(success)}/{len(included)} succeeded")
            st.success(
                f"Download complete! {len(success)} PDFs saved to "
                f"`{workflow.downloader.get_download_dir()}`"
            )
            failures = [r for r in results if r[1] is None]
            if failures:
                with st.expander(f"⚠️ {len(failures)} failed / 下载失败"):
                    for p, _ in failures:
                        st.write(f"- {p.title[:100]}")

        st.divider()
        st.subheader("Export / 导出")
        col1, col2 = st.columns(2)
        with col1:
            csv_data = workflow.export_included_csv(state)
            st.download_button(
                "📄 Download CSV", csv_data,
                file_name=f"literature_review_{state.topic[:30]}.csv",
                mime="text/csv", use_container_width=True,
            )
        with col2:
            ris_data = workflow.export_included_ris(state)
            st.download_button(
                "📚 Download RIS (EndNote/Zotero)", ris_data,
                file_name=f"literature_review_{state.topic[:30]}.ris",
                mime="application/x-research-info-systems", use_container_width=True,
            )


def render_threshold_calibrator(workflow, state, current_threshold: float) -> None:
    """Turn '0.15 feels about right' into a defensible, measured threshold.

    The user labels a handful of papers they already know the verdict for, and
    the midpoint between the two groups becomes the suggested cut-off. Also
    shows the score distribution so a threshold can be sanity-checked visually
    before it is applied.
    """
    papers = [r.paper for r in state.prisma.records.values()]
    if len(papers) < 4:
        return

    with st.expander("🎯 Calibrate Threshold / 标定筛选阈值", expanded=False):
        st.caption(
            "打分是**相对本次结果集**的（0.1 在不同检索里含义不同）。"
            "给几篇你已知结论的文献打标，就能算出一个有依据的阈值，"
            "而不是沿用默认值。 / Scores are relative to this result set."
        )

        scored = sorted((p for p in papers if p.relevance_score > 0),
                        key=lambda p: p.relevance_score, reverse=True)
        if not scored:
            st.info("当前结果没有相关性得分，请先运行检索。 / No scores yet — run a search first.")
            return

        # Distribution first: it makes an obvious threshold gap visible before
        # the user commits to labelling anything.
        st.markdown("**Score distribution / 得分分布**")
        bins = 10
        hist = [0] * bins
        for p in scored:
            idx = min(bins - 1, int(p.relevance_score * bins))
            hist[idx] += 1
        max_h = max(hist) or 1
        for i, count in enumerate(hist):
            lo, hi = i / bins, (i + 1) / bins
            bar = "█" * round(count / max_h * 40)
            marker = " ←当前阈值" if lo <= current_threshold <= hi else ""
            st.text(f"{lo:.1f}–{hi:.1f} │{bar} {count}{marker}")

        st.markdown("---")
        seed = scored[:12]
        st.markdown(f"**Label known papers / 给已知文献打标** (从 {len(seed)} 篇候选中选)")
        picked: list[tuple] = []
        for p in seed:
            key = f"cal_{p.id[:30]}"
            label = st.selectbox(
                f"{p.title[:80]} ({p.relevance_score:.3f})",
                ["— 未标注 —", "相关 relevant", "不相关 irrelevant"],
                key=key,
                label_visibility="collapsed",
            )
            if label.startswith("相关"):
                picked.append((p, True))
            elif label.startswith("不相关"):
                picked.append((p, False))

        if st.button("📐 Compute Threshold / 计算阈值", disabled=len(picked) < 2):
            rel = [p for p, is_rel in picked if is_rel]
            irr = [p for p, is_rel in picked if not is_rel]
            value, note = RelevanceFilter.calibrate_threshold(rel, irr)
            st.session_state["screen_threshold"] = value
            st.success(f"**建议阈值 / Suggested threshold: {value:.3f}**")
            st.info(note)
            st.caption("已写回上方滑块，可直接点「自动初筛」。 / Written back to the slider above.")
            if len(rel) < 3 or len(irr) < 3:
                st.warning(
                    "样本偏少，阈值不稳定。每类标 3 篇以上结论才比较可靠。 / "
                    "Small sample: label 3+ per class for a trustworthy cut-off."
                )

        st.markdown("---")
        with st.expander("Why these scores? / 得分构成"):
            bd = {b.paper_id: b for b in workflow.filters.last_breakdown}
            if not bd:
                st.caption("运行检索后显示。 / Available after a search.")
                return
            st.dataframe(
                [
                    {
                        "Title": p.title[:70],
                        "Word TF-IDF": bd[p.id].word_score if p.id in bd else None,
                        "Char n-gram": bd[p.id].char_score if p.id in bd else None,
                        "Term coverage": bd[p.id].coverage if p.id in bd else None,
                        "Final": p.relevance_score,
                    }
                    for p in scored[:40] if p.id in bd
                ],
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "Word TF-IDF 抓字面重合；Char n-gram 抓形态变体"
                "（phenotype / phenotyping / phenotypic）；Term coverage 抓查询概念命中率，不受摘要长度影响。"
            )


def render_manual_queue(workflow, state, queue, stage):
    if not queue:
        st.success("该阶段已全部处理完 / nothing pending in this stage")
        return
    st.subheader(f"Manual Screening Queue / 手动筛选队列 ({len(queue)} remaining)")
    for record in queue[:20]:
        paper = record.paper
        with st.container():
            col1, col2, col3, col4 = st.columns([5, 1, 1, 1])
            with col1:
                st.markdown(f"**{paper.title[:150]}** ({paper.year or '?'})")
                st.caption(
                    f"Source: {record.source} | Citations: {paper.citation_count} | "
                    f"Relevance: {record.relevance_score:.3f}"
                )
            with col2:
                if st.button("✅ Accept", key=f"acc_{stage.value}_{paper.id[:30]}", use_container_width=True):
                    workflow.screen_paper(state, paper.id, ScreeningDecision.ACCEPT, stage=stage)
                    st.session_state["review_state"] = state
                    persist(state)
                    st.rerun()
            with col3:
                if st.button("❓ Maybe", key=f"may_{stage.value}_{paper.id[:30]}", use_container_width=True):
                    workflow.screen_paper(state, paper.id, ScreeningDecision.MAYBE, stage=stage)
                    st.session_state["review_state"] = state
                    persist(state)
                    st.rerun()
            with col4:
                if st.button("❌ Reject", key=f"rej_{stage.value}_{paper.id[:30]}", use_container_width=True):
                    workflow.screen_paper(state, paper.id, ScreeningDecision.REJECT, stage=stage)
                    st.session_state["review_state"] = state
                    persist(state)
                    st.rerun()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    st.title("📚 LEExtractor")
    st.caption(
        "Systematic Literature Review Toolkit — "
        "Scoping → Systematic Search → Snowballing → PRISMA (two-stage) → PDF Download"
    )

    years_back, max_papers = render_sidebar()

    if "workflow" not in st.session_state:
        st.session_state["workflow"] = LiteratureReviewWorkflow()

    workflow = st.session_state["workflow"]

    # --- restore autosaved progress (first load only) --------------------
    if "review_state" not in st.session_state and not st.session_state.get("restored_once"):
        st.session_state["restored_once"] = True
        restored = load_state()
        if restored is not None:
            st.session_state["review_state"] = restored
            st.session_state["topic"] = restored.topic
            st.session_state["direction"] = restored.research_direction

    state = st.session_state.get("review_state")
    if (
        state is not None
        and st.session_state.get("restored_once")
        and st.session_state.get("show_restore_note", True)
    ):
        sessions = list_sessions()
        if sessions:
            age = session_age_text(sessions[0]["saved_at"])
            st.info(
                f"💾 已自动恢复上次进度（{age}）：**{state.topic}**"
                " — 进度会自动保存，刷新页面不会丢。"
            )
            st.session_state["show_restore_note"] = False

    if state is not None:
        render_phase_indicator(state.phase)
        st.divider()

    tab1, tab2, tab3, tab4 = st.tabs([
        "1. Scoping / 领域概览",
        "2. Systematic Search / 系统检索",
        "3. Snowballing / 引文追溯",
        "4. PRISMA & Export / 筛选与导出",
    ])

    with tab1:
        render_scoping(workflow, years_back)
    with tab2:
        render_systematic_search(workflow, max_papers, years_back)
    with tab3:
        render_snowballing(workflow)
    with tab4:
        render_prisma(workflow)

    st.divider()
    st.caption(
        "Methodology: Gusenbauer (2024) + TARCiS statement + PRISMA 2020 | "
        "Sources: Semantic Scholar · OpenAlex · arXiv · Crossref | "
        "PDF: arXiv · OpenAccess (OpenAlex/Unpaywall)"
    )


if __name__ == "__main__":
    main()
