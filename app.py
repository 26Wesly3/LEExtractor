"""LEExtractor: focused discovery workspace with optional PRISMA reporting."""

import json
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

from litsearch import __version__
from litsearch.bundle import build_evidence_pack
from litsearch.config import openalex_api_key, s2_key_configured, set_env_value
from litsearch.diagnostics import get_diagnostics, reset_diagnostics
from litsearch.evidence import EvidenceGraph, corpus_papers
from litsearch.export import to_bibtex, to_csv, to_ris
from litsearch.filters import RelevanceFilter
from litsearch.persistence import (
    load_state,
    save_state,
    save_state_as,
    state_from_dict,
    state_to_dict,
)
from litsearch.prisma import FULL_TEXT_EXCLUSION_REASONS, ScreeningDecision, ScreeningStage
from litsearch.search import LiteratureReviewWorkflow, ReviewState

st.set_page_config(page_title="LEExtractor", page_icon="📚", layout="wide")


def t(zh, en):
    return en if st.session_state.get("language") == "English" else zh


def persist(state):
    try:
        save_state(state)
    except OSError as exc:
        st.warning(t("保存失败：", "Save failed: ") + str(exc))


def go(page):
    st.session_state["page"] = page


def load_demo():
    from litsearch.demo import demo_state
    state = demo_state()
    st.session_state["review_state"] = state
    st.session_state["topic_input"] = state.topic
    st.session_state["direction_input"] = state.research_direction
    persist(state)


def diagnostics(state=None):
    events = get_diagnostics().events
    saved = (state.search_manifest.get("diagnostics", []) if state else [])
    if events or saved:
        st.warning(t("部分数据源请求未完成，结果可能不完整。", "Some provider requests failed; results may be incomplete."))
        with st.expander(t("查看原因与建议", "Provider diagnostics")):
            for event in events:
                st.text(event.short(250))
            if not events:
                st.json(saved)
            st.caption(t("限流时降低规模或配置数据源密钥；参数错误需要核对接口。", "For rate limits, reduce the search size or configure keys. Contract errors require checking the provider API."))


def save_key(env_name, widget_key):
    value = st.session_state.get(widget_key, "").strip()
    if value:
        set_env_value(env_name, value)
        st.session_state[widget_key] = ""
        st.session_state["key_saved"] = True


def sidebar():
    st.sidebar.title("📚 LEExtractor")
    st.sidebar.selectbox("语言 / Language", ["中文", "English"], key="language")
    review = st.sidebar.checkbox(t("系统综述模式（PRISMA）", "Systematic review mode (PRISMA)"), key="review_mode")
    pages = ["explore", "papers", "expand", "landscape", "export"]
    if review:
        pages.insert(4, "review")
    labels = {"explore": t("研究探索", "Explore"), "papers": t("文献结果", "Papers"),
              "expand": t("引文扩展", "Expand citations"), "landscape": t("证据版图", "Evidence landscape"),
              "review": t("筛选与 PRISMA", "Screening & PRISMA"), "export": t("保存与导出", "Save & export")}
    if st.session_state.get("page") not in pages:
        st.session_state["page"] = "explore"
    page = st.sidebar.radio(t("工作区", "Workspace"), pages, format_func=labels.get, key="page")
    with st.sidebar.expander(t("检索范围", "Search scope")):
        years_back = st.slider(t("回溯年数", "Years back"), 5, 50, 20, key="years_back",
                               help=t("年份条件用于数据库检索；引文扩展默认允许更早的基础文献。", "Applies to database search. Citation expansion can include older foundational papers."))
        max_papers = st.slider(t("最多保留文献", "Maximum retained papers"), 50, 500, 200, 50, key="max_papers",
                               help=t("多源结果合并、排序后保留前 N 篇；值越大，后续阅读和 API 请求越多。", "Keep the top N papers after merging and ranking. Larger searches require more reading and API calls."))
    with st.sidebar.expander(t("数据源设置", "Provider settings")):
        st.caption("Semantic Scholar: " + t("已配置", "configured") if s2_key_configured() else "Semantic Scholar: " + t("未配置", "not configured"))
        st.text_input("S2_API_KEY", type="password", key="s2_key")
        st.button(t("保存 S2 密钥", "Save S2 key"), on_click=save_key, args=("S2_API_KEY", "s2_key"))
        st.link_button(t("申请 S2 密钥", "Get an S2 key"), "https://www.semanticscholar.org/product/api#api-key-form")
        st.caption("OpenAlex: " + t("已配置", "configured") if openalex_api_key() else "OpenAlex: " + t("未配置", "not configured"))
        st.text_input("OPENALEX_API_KEY", type="password", key="oa_key")
        st.button(t("保存 OpenAlex 密钥", "Save OpenAlex key"), on_click=save_key, args=("OPENALEX_API_KEY", "oa_key"))
        st.link_button(t("申请 OpenAlex 密钥", "Get an OpenAlex key"), "https://openalex.org/settings/api")
        st.caption(t("保存后立即生效；密钥只写入本机 .env。", "Saved keys apply immediately and stay in the local .env file."))
    if st.session_state.pop("key_saved", False):
        st.sidebar.success(t("密钥已保存", "Key saved"))
    st.sidebar.caption(f"v{__version__}")
    return page, years_back, max_papers


def run_action(action):
    reset_diagnostics()
    try:
        state = action()
        st.session_state["review_state"] = state
        persist(state)
        st.rerun()
    except (ValueError, OSError) as exc:
        st.error(str(exc))


def explore(workflow, years_back, max_papers):
    st.header(t("从研究问题开始", "Start with a research question"))
    st.caption(t("先找到相关文献，再决定是否扩展引文或开展系统综述。", "Find relevant papers, then decide whether to expand citations or run a systematic review."))
    state = st.session_state.get("review_state")
    topic = st.text_input(t("检索关键词", "Search keywords"), value=state.topic if state else "",
                          placeholder="deep learning plant phenotyping", key="topic_input")
    direction = st.text_area(t("你关注的具体问题", "Your research focus"), value=state.research_direction if state else "",
                             placeholder=t("研究对象、方法、任务和场景，例如：使用图像预测小麦表型", "Object, method, task and context, e.g. predicting wheat traits from images"), key="direction_input")
    st.caption(t("当前默认采用词法排序，不自动翻译中文。检索国际数据库时建议填写英文关键词；中文研究问题可保留作记录。", "Default ranking is lexical and does not translate queries. English keywords work best with international databases; retain your original research question for context."))
    col1, col2 = st.columns(2)
    with col1:
        if st.button(t("检索文献", "Search papers"), type="primary", disabled=not topic.strip(), width="stretch"):
            fresh = ReviewState(topic=topic.strip(), research_direction=direction.strip() or topic.strip(), start_time=time.time())
            with st.spinner(t("合并多源检索结果…", "Searching and merging provider results…")):
                run_action(lambda: workflow.systematic_search(fresh, years_back=years_back, max_papers=max_papers))
    with col2:
        if st.button(t("先看领域概览", "Scope the field first"), disabled=not topic.strip(), width="stretch"):
            with st.spinner(t("正在分析检索样本…", "Analyzing a scoping sample…")):
                run_action(lambda: workflow.scope_topic(topic.strip(), direction.strip(), years_back=years_back))
    if state:
        st.divider()
        st.success(t(f"当前项目：{state.topic}", f"Current project: {state.topic}"))
        if state.scoping_papers:
            cols = st.columns(3)
            cols[0].metric(t("样本文献", "Scoping papers"), len(state.scoping_papers))
            cols[1].metric(t("主要期刊", "Journals"), len(state.key_journals))
            cols[2].metric(t("关键词簇", "Keyword clusters"), len(state.topic_clusters))
            for name, terms in list(state.topic_clusters.items())[:6]:
                st.write(f"**{name}** · {', '.join(terms)}")
            st.caption(t("关键词共现仅反映检索样本，不等同于全领域语义主题。", "Keyword co-occurrence describes this sample, not the entire field or semantic topics."))
        if not state.search_papers:
            if st.button(t("在当前项目执行完整检索", "Run a full search for this project"), type="primary"):
                with st.spinner(t("检索中…", "Searching…")):
                    run_action(lambda: workflow.systematic_search(state, years_back=years_back, max_papers=max_papers))
        else:
            st.button(t("查看文献结果 →", "View papers →"), on_click=go, args=("papers",))
        diagnostics(state)
    with st.expander(t("离线演示", "Offline demo")):
        st.caption(t("使用明确标注的模拟文献验证交互，不调用外部 API。", "Use explicitly synthetic papers to exercise the workflow without external APIs."))
        st.button(t("加载模拟演示", "Load synthetic demo"), on_click=load_demo)


def paper_card(paper, index=0):
    with st.container(border=True):
        st.markdown(f"**{index + 1}. {paper.title}**")
        st.caption(f"{paper.year or '?'} · {paper.venue or paper.source} · " + t(f"引用 {paper.citation_count} · 相关度 {paper.relevance_score:.3f}", f"Citations {paper.citation_count} · Relevance {paper.relevance_score:.3f}"))
        if paper.url and paper.url.startswith(("https://", "http://")):
            st.link_button(t("论文页面", "Paper page"), paper.url)
        with st.expander(t("摘要与发现依据", "Abstract & discovery evidence")):
            st.write(paper.abstract or t("数据源未提供摘要", "No abstract supplied by the provider"))
            st.caption(t("为什么找到这篇论文", "Why this paper appeared"))
            for trace in paper.discovery_traces:
                st.write({"method": trace.method, "provider": trace.provider, "query": trace.query,
                          "seed": trace.seed_id, "round": trace.round_no, "score": trace.score,
                          "evidence": trace.evidence_ids})
            if paper.score_breakdown:
                st.json(paper.score_breakdown)
            st.caption(t("这些信号解释发现过程，不代表论文质量，也不替代阅读。", "These signals explain discovery, not research quality or full-text assessment."))


def papers_page(state):
    st.header(t("文献结果", "Paper results"))
    papers = corpus_papers(state)
    def providers(paper):
        return {paper.source} | {trace.provider for trace in paper.discovery_traces if trace.provider and trace.method != "demo_fixture"}
    st.caption(t(f"合并后共 {len(papers)} 篇。先缩小列表，再挑选种子论文。", f"{len(papers)} merged papers. Narrow the list before choosing seeds."))
    a, b, c = st.columns(3)
    threshold = a.slider(t("最低相关度", "Minimum relevance"), 0.0, 1.0, 0.0, 0.05, key="result_threshold")
    sources = b.multiselect(t("数据源", "Providers"), sorted({source for p in papers for source in providers(p)}), key="result_sources")
    sort = c.selectbox(t("排序", "Sort by"), ["relevance", "citations", "year"], format_func=lambda k: {"relevance": t("相关度", "Relevance"), "citations": t("引用数", "Citations"), "year": t("最新发表", "Newest")}[k])
    text = st.text_input(t("标题或摘要包含", "Title or abstract contains"), key="result_text")
    years = [p.year for p in papers if p.year]
    year_range = None
    if years and min(years) < max(years):
        year_range = st.slider(t("发表年份", "Publication years"), min(years), max(years), (min(years), max(years)))
    filtered = [p for p in papers if p.relevance_score >= threshold and (not sources or set(sources) & providers(p))
                and (not text or text.casefold() in f"{p.title} {p.abstract or ''}".casefold())
                and (not year_range or (p.year and year_range[0] <= p.year <= year_range[1]))]
    filtered.sort(key=lambda p: {"relevance": p.relevance_score, "citations": p.citation_count, "year": p.year or 0}[sort], reverse=True)
    page_count = max(1, (len(filtered) + 9) // 10)
    page = st.number_input(t("页码", "Page"), 1, page_count, 1)
    st.caption(t(f"符合条件 {len(filtered)} 篇，每页显示 10 篇。", f"{len(filtered)} matches, 10 papers per page."))
    for index, paper in enumerate(filtered[(page - 1) * 10:page * 10], start=(page - 1) * 10):
        paper_card(paper, index)
    if not filtered:
        st.info(t("没有符合条件的文献，请放宽筛选条件。", "No matches. Relax the filters."))
    st.button(t("扩展引文 →", "Expand citations →"), on_click=go, args=("expand",))
    diagnostics(state)


def expand_page(workflow, state):
    st.header(t("从已有文献继续扩展", "Expand from your existing papers"))
    st.caption(t("引文追溯找基础与后续工作；文献耦合找共享参考文献的论文，共被引找被共同引用的论文。", "Citation tracing finds foundations and later work. Coupling uses shared references; co-citation uses common citing papers."))
    if not state.search_papers:
        st.info(t("请先执行文献检索。", "Run a paper search first."))
        return
    if state.search_manifest.get("mode") == "synthetic_demo":
        from litsearch.demo import DemoSource
        workflow = LiteratureReviewWorkflow(DemoSource())
        st.info(t("演示模式使用模拟引文，所有扩展均在本机完成。", "Demo expansion uses synthetic citations entirely on this machine."))
    a, b, c = st.columns(3)
    seeds = a.number_input(t("种子论文数", "Seed papers"), 1, min(30, len(state.search_papers)), min(5, len(state.search_papers)),
                           help=t("从检索结果中取相关度最高的 N 篇作为起点。", "Start from the N highest-ranked search results."))
    rounds = b.number_input(t("最多追溯轮次", "Maximum rounds"), 1, 5, 2, help=t("每轮最多选择 20 篇新文献继续扩展；新增少于 5 篇时提前结束。", "Up to 20 new papers seed the next round. Stop early when fewer than 5 papers are added."))
    size = c.number_input(t("每篇每方向上限", "Papers per seed per direction"), 10, 100, 20, 10)
    st.caption(t("增大范围会增加 API 请求和阅读工作量。每轮保存可恢复的检查点。", "Larger ranges increase API calls and reading workload. Each completed round saves a recoverable checkpoint."))
    resume = bool(state.snowball_result and not state.snowball_result.completed and state.snowball_result.parameters)
    def run_snowball():
        progress = st.progress(0.0)
        status = st.empty()
        def on_round(number, total, found):
            progress.progress(min(1.0, number / total))
            status.write(t(f"第 {number} 轮新增 {found} 篇", f"Round {number}: {found} new papers"))
            persist(state)
        params = state.snowball_result.parameters if resume else {}
        return workflow.run_snowballing(state, num_seeds=len(params.get("seed_ids", [])) or seeds,
                                       max_rounds=rounds, max_per_direction=params.get("max_per_direction", size),
                                       min_citations=params.get("min_citations", 0), year_from=params.get("year_from", 1900),
                                       year_to=params.get("year_to"), on_round=on_round, resume=resume)
    a, b = st.columns(2)
    if a.button(t("继续上次追溯" if resume else "追溯引文", "Resume citation tracing" if resume else "Trace citations"), type="primary", width="stretch"):
        with st.spinner(t("正在追溯并保存…", "Tracing and saving checkpoints…")):
            run_action(run_snowball)
    if b.button(t("查找关联文献（BC / CC）", "Find related papers (BC / CC)"), width="stretch"):
        with st.spinner(t("分析共享参考文献与共被引关系…", "Analyzing coupling and co-citation…")):
            run_action(lambda: workflow.find_similar(state, num_seeds=seeds))
    sn = state.snowball_result
    if sn:
        st.metric(t("新增文献（不含原有文献）", "New papers (excluding the original corpus)"), sn.total_discovered)
        st.dataframe([{t("轮次", "Round"): r.round_number, t("返回记录", "Fetched"): r.raw_count, t("去重新增", "New unique"): r.unique_count,
                       t("保留文献", "Retained"): r.count, t("相关度 ≥ 0.15", "Relevance ≥ 0.15"): r.relevant_count,
                       t("重复率", "Duplicate rate"): round(1 - r.unique_count / r.raw_count, 3) if r.raw_count else 0,
                       t("累计新增", "Cumulative new"): r.cumulative_unique} for r in sn.rounds], hide_index=True, width="stretch")
        if sn.saturated:
            st.info(t("本轮新增少于 5 篇，已按当前规则停止。可检查种子和关键词后决定是否扩大范围；这不代表找齐了全部文献。", "Fewer than 5 papers were added, so the current rule stopped expansion. Revisit seeds or keywords if needed; this does not establish complete coverage."))
    if state.similar_papers:
        st.success(t(f"找到 {len(state.similar_papers)} 篇关联文献，已加入文献结果。", f"{len(state.similar_papers)} related papers added to your results."))
    diagnostics(state)
    st.button(t("查看证据版图 →", "View evidence landscape →"), on_click=go, args=("landscape",))


def landscape_page(state):
    st.header(t("证据版图", "Evidence landscape"))
    graph = EvidenceGraph(corpus_papers(state))
    summary = graph.summary()
    a, b, c = st.columns(3)
    a.metric(t("文献节点", "Paper nodes"), summary["nodes"])
    b.metric(t("已观察引文边", "Observed citation edges"), summary["edges"])
    c.metric(t("引文社群", "Citation communities"), len(summary["communities"]))
    st.caption(t("箭头从引用者指向被引用者。PageRank 与社群仅描述当前证据集，不评价质量、不推断创新性。", "Arrows point from citing to cited papers. PageRank and communities describe this corpus, not quality or novelty."))
    if summary["timeline"]:
        st.bar_chart({"year": list(summary["timeline"]), "papers": list(summary["timeline"].values())}, x="year", y="papers")
    names = {p.canonical_id: p.title for p in graph.papers}
    if summary["central_papers"]:
        st.dataframe([{t("论文", "Paper"): names[row["paper_id"]], "PageRank": row["score"]} for row in summary["central_papers"]], hide_index=True, width="stretch")
        edges = list(graph.graph.edges)[:100]
        nodes = sorted({node for edge in edges for node in edge})
        labels = {key: f"n{i}" for i, key in enumerate(nodes)}
        dot = ['digraph evidence { rankdir=TB; node [shape=box, style=rounded, fontsize=12];']
        for key in nodes:
            title = "\n".join(textwrap.wrap(names[key][:100], width=32))
            dot.append(f'{labels[key]} [label={json.dumps(title)}];')
        dot += [f"{labels[u]} -> {labels[v]};" for u, v in edges]
        st.graphviz_chart("\n".join([*dot, "}"]))
        if len(graph.graph.edges) > 100:
            st.caption(t("预览仅显示前 100 条边；导出包含完整图。", "Preview shows the first 100 edges; exports contain the full graph."))
        with st.expander(t("查询证据路径", "Find an evidence path")):
            source = st.selectbox(t("起点", "From"), list(names), format_func=names.get, key="path_source")
            target = st.selectbox(t("终点", "To"), list(names), format_func=names.get, key="path_target")
            path = graph.path(source, target)
            st.write(" → ".join(names[k] for k in path) if path else t("当前证据中没有有向引文路径。", "No directed citation path exists in the observed evidence."))
    else:
        st.info(t("目前没有能在证据集内对齐的引文边。可先扩展引文，系统不会补造关系。", "No in-corpus citation edges can currently be resolved. Expand citations to collect evidence."))


def apply_threshold(relevant, irrelevant):
    value, note = RelevanceFilter.calibrate_threshold(relevant, irrelevant)
    st.session_state["screen_threshold"] = value
    st.session_state["calibration_note"] = note


def review_page(workflow, state):
    st.header(t("筛选与 PRISMA", "Screening & PRISMA"))
    stage_name = st.radio(t("评估阶段", "Assessment stage"), ["title", "full"], horizontal=True,
                          format_func=lambda k: t("标题与摘要", "Title & abstract") if k == "title" else t("全文评估", "Full text"))
    stage = ScreeningStage.TITLE_ABSTRACT if stage_name == "title" else ScreeningStage.FULL_TEXT
    if stage_name == "title":
        threshold = st.slider(t("自动初筛阈值", "Automatic screening threshold"), 0.0, 1.0, 0.15, 0.01, key="screen_threshold")
        st.caption(t("分数随候选集变化，只用于初筛；全文纳入需人工评估。", "Scores depend on the candidate set and support preliminary screening; full-text inclusion requires manual assessment."))
        if st.button(t("按阈值处理待筛文献", "Screen pending papers by threshold")):
            workflow.auto_screen(state, relevance_threshold=threshold)
            persist(state)
            st.rerun()
        with st.expander(t("用已知文献标定阈值", "Calibrate using known papers")):
            rel, irr = [], []
            for p in corpus_papers(state)[:12]:
                label = st.selectbox(p.title, ["unlabelled", "relevant", "irrelevant"], key=f"cal_{p.canonical_id}",
                                     format_func=lambda k: {"unlabelled": t("未标注", "Unlabelled"), "relevant": t("相关", "Relevant"), "irrelevant": t("不相关", "Irrelevant")}[k])
                if label == "relevant":
                    rel.append(p)
                elif label == "irrelevant":
                    irr.append(p)
            st.button(t("计算并应用阈值", "Compute & apply threshold"), disabled=len(rel) + len(irr) < 2,
                      on_click=apply_threshold, args=(rel, irr))
            if st.session_state.get("calibration_note"):
                st.info(st.session_state["calibration_note"])
    queue = workflow.get_screening_queue(state, stage)
    # MAYBE remains actionable instead of disappearing from the queue.
    if stage_name == "title":
        queue += [r for r in state.prisma.records.values() if r.screening_decision == ScreeningDecision.MAYBE]
    else:
        queue += [r for r in state.prisma.records.values() if r.passed_screening and r.full_text_decision == ScreeningDecision.MAYBE]
    st.subheader(t(f"待处理 {len(queue)} 篇", f"{len(queue)} papers to assess"))
    for record in queue[:10]:
        paper = record.paper
        key = f"{stage.value}_{paper.canonical_id}"
        with st.container(border=True):
            st.write(f"**{paper.title}** ({paper.year or '?'})")
            if paper.abstract:
                with st.expander(t("摘要", "Abstract")):
                    st.write(paper.abstract)
            reason = st.text_input(t("决定理由", "Decision reason"), key=f"reason_{key}")
            if stage_name == "full":
                retrieved = st.checkbox(t("已获取并阅读全文", "Full text retrieved and assessed"), value=record.full_text_retrieved, key=f"read_{key}")
                if st.button(t("尝试下载开放获取全文", "Try downloading open-access full text"), key=f"pdf_{key}"):
                    with st.spinner(t("查找开放获取版本…", "Looking for open-access copies…")):
                        path = workflow.downloader.download_pdf(paper)
                    if path:
                        workflow.mark_full_text_retrieved(state, paper.id, True)
                        persist(state)
                        st.success(str(path))
                    else:
                        st.info(t("未获取到 OA 版本。可通过机构访问或手动获取；下载失败不会自动排除文献。", "No OA copy retrieved. Try institutional or manual access. A failed download does not automatically exclude the paper."))
                reason_labels = [value.rsplit("/", 1)[0 if st.session_state["language"] == "中文" else -1].strip() for value in FULL_TEXT_EXCLUSION_REASONS]
                reason_choice = st.selectbox(t("全文排除原因", "Full-text exclusion reason"), reason_labels, key=f"exclude_{key}")
                exclusion = FULL_TEXT_EXCLUSION_REASONS[reason_labels.index(reason_choice)]
            else:
                retrieved, exclusion = True, ""
            a, b, c = st.columns(3)
            decisions = [(a, ScreeningDecision.ACCEPT, t("纳入", "Accept")), (b, ScreeningDecision.MAYBE, t("待复核", "Maybe")), (c, ScreeningDecision.REJECT, t("排除", "Reject"))]
            for column, decision, label in decisions:
                if column.button(label, key=f"{decision.value}_{key}", disabled=decision == ScreeningDecision.ACCEPT and not retrieved, width="stretch"):
                    if stage_name == "full" and decision == ScreeningDecision.REJECT and exclusion == FULL_TEXT_EXCLUSION_REASONS[0]:
                        workflow.mark_full_text_retrieved(state, paper.id, False)
                    else:
                        workflow.screen_paper(state, paper.id, decision, reason or (exclusion if decision == ScreeningDecision.REJECT else ""), stage=stage)
                    persist(state)
                    st.rerun()
    report = state.prisma.generate_report()
    st.divider()
    st.caption(t("报告仅映射已记录的筛选状态。未完成全文评估时属于初筛结果。", "The report reflects recorded screening decisions. Without full-text assessment, inclusion is preliminary."))
    st.json(report.to_flow_dict(), expanded=False)
    st.download_button(t("下载 PRISMA 流程图 Markdown", "Download PRISMA flow Markdown"), report.to_mermaid(), "prisma_flow.md", mime="text/markdown")


def export_page(state):
    st.header(t("保存与导出", "Save & export"))
    papers = corpus_papers(state)
    selection = st.radio(t("导出范围", "Export selection"), ["all", "included"], horizontal=True,
                         format_func=lambda value: t("全部发现文献", "All discovered papers") if value == "all" else t("筛选纳入文献", "Included papers"))
    selected = papers if selection == "all" else state.prisma.get_included_papers()
    st.caption(t(f"当前选择 {len(selected)} 篇。RIS / BibTeX 可导入 Zotero；证据包用于复现与 AI 交接。", f"{len(selected)} selected papers. Import RIS / BibTeX into Zotero; use the evidence pack for reproducibility and AI handoff."))
    a, b, c = st.columns(3)
    a.download_button("CSV", "\ufeff" + to_csv(selected, include_abstract=True), "papers.csv", mime="text/csv")
    b.download_button("RIS", to_ris(selected), "references.ris", mime="application/x-research-info-systems")
    c.download_button("BibTeX", to_bibtex(selected), "references.bib", mime="text/plain")
    st.download_button(t("下载完整证据包 ZIP", "Download complete evidence pack ZIP"), build_evidence_pack(state), "evidence_pack.zip", mime="application/zip")
    st.caption(t("完整证据包始终包含整个项目，以及检索配置、发现路径、引文图和可恢复会话。", "The complete pack always contains the whole project, search configuration, discovery traces, citation graph and restorable session."))
    st.download_button(t("备份当前会话 JSON", "Backup session JSON"), json.dumps(state_to_dict(state), ensure_ascii=False), "session.json", mime="application/json")
    if st.button(t("保留项目快照到本机", "Save a named local snapshot")):
        st.success(save_state_as(state))
    upload = st.file_uploader(t("恢复会话 JSON", "Restore session JSON"), type=["json"])
    if upload and st.button(t("恢复该会话", "Restore this session")):
        try:
            restored = state_from_dict(json.load(upload))
            st.session_state["review_state"] = restored
            persist(restored)
            st.rerun()
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            st.error(t("会话格式无法读取：", "Invalid session format: ") + str(exc))


def main():
    st.markdown("<style>.block-container{max-width:1120px;padding-top:2rem}div[data-testid='stCaptionContainer']{font-size:0.95rem}</style>", unsafe_allow_html=True)
    if "review_state" not in st.session_state and not st.session_state.get("restored_once"):
        st.session_state["restored_once"] = True
        restored = load_state()
        if restored:
            st.session_state["review_state"] = restored
            st.session_state["restore_note"] = True
    if "workflow" not in st.session_state:
        st.session_state["workflow"] = LiteratureReviewWorkflow()
    page, years_back, max_papers = sidebar()
    state = st.session_state.get("review_state")
    if st.session_state.pop("restore_note", False):
        st.info(t("已恢复上次保存的项目。", "Restored your last saved project."))
    if state and state.search_manifest.get("mode") == "synthetic_demo":
        st.warning(t("当前为模拟演示文献，不可作为真实科研证据。", "Synthetic demo papers: do not use as real research evidence."))
    workflow = st.session_state["workflow"]
    if page == "explore":
        explore(workflow, years_back, max_papers)
    elif state is None:
        st.info(t("请先在研究探索中输入课题，或加载离线演示。", "Start a project in Explore or load the offline demo."))
    elif page == "papers":
        papers_page(state)
    elif page == "expand":
        expand_page(workflow, state)
    elif page == "landscape":
        landscape_page(state)
    elif page == "review":
        review_page(workflow, state)
    elif page == "export":
        export_page(state)


if __name__ == "__main__":
    main()
