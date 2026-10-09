"""Structured research intent and per-database query planning.

Two things live here, and it is worth keeping them apart in your head:

**Intent parsing** is *rule-based and shallow*. It looks for cue patterns
("使用 X 预测 Y", "deep learning for Y") to split a fuzzy direction into
object / method / task / scenario slots. It is not an LLM, it does not
understand the sentence, and it reports a ``confidence``, the ``unparsed``
remainder and a list of ``warnings`` so a human can see exactly what it failed
to place. Slots are either filled with a real term taken from the input, or
left empty — never padded with a guess.

**Query planning** is the part that is genuinely mechanical: each provider has
a different query language, and sending the same bare string to all of them
leaves recall on the table.

* Semantic Scholar — the raw natural-language sentence (S2 does its own ranking).
* OpenAlex — keyword string for ``search=``, with ``publication_year`` as filter.
* arXiv — field-prefixed expression (``ti:`` / ``abs:``) when at least two
  concept groups were actually recovered, otherwise a plain ``all:()``.
* Crossref — keyword string for bibliographic lookup.

The generated strings are recorded in the search manifest. This matters beyond
convenience: PRISMA 2020 requires reporting the exact search string per
database, and until now the manifest only stored the single raw input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Cue vocabularies
# ---------------------------------------------------------------------------

# Chinese: cues are function words / verbs that mark a slot boundary.
_ZH_METHOD_CUES = ("使用", "基于", "采用", "利用", "通过", "借助", "运用", "依托")
_ZH_TASK_CUES = (
    "预测", "预估", "估计", "估算", "识别", "分类", "检测", "分割", "提取",
    "评估", "监测", "分析", "测量", "重建", "计数", "反演", "诊断", "预警",
)
_ZH_SCENARIO_CUES = (
    "田间", "温室", "大棚", "室外", "室内", "野外", "高通量",
    "条件下", "环境下", "场景下", "可控环境",
)
_ZH_PARTICLES = set("的地得和与及在是对为用并或了把被从向到")
_ZH_BREAK = "，,。.；;：:！!？?、（）()[]【】“”\"'《》/\\|-—…\n\t"

# English: method is matched against a vocabulary *and* an introducer pattern,
# because "the words after using" is only sometimes the method.
_EN_METHOD_PHRASES = (
    "deep learning", "machine learning", "transfer learning",
    "reinforcement learning", "federated learning", "contrastive learning",
    "convolutional neural network", "convolutional network", "graph neural network",
    "recurrent neural network", "neural network", "random forest",
    "support vector machine", "gradient boosting", "decision tree",
    "attention mechanism", "self-attention", "large language model",
    "remote sensing", "hyperspectral imaging", "multispectral imaging",
    "point cloud", "digital twin", "time series",
)
_EN_METHOD_WORDS = (
    "cnn", "rnn", "lstm", "gru", "gan", "yolo", "resnet", "unet", "vit",
    "svm", "xgboost", "lightgbm", "transformer", "bert", "gpt", "diffusion",
    "regression", "clustering", "embedding", "lidar", "uav", "sar",
)
_EN_METHOD_INTRO = ("using", "via", "based on", "through", "by means of", "with")

# Task: stem -> surface regex. We emit the canonical lemma, not the raw stem,
# so the query reads "estimate" rather than "estim".
_EN_TASK_STEMS: tuple[tuple[str, str], ...] = (
    ("predict", r"predict(?:ion|ions|ive|s|ed|ing)?"),
    ("forecast", r"forecast(?:ing|s)?"),
    ("estimate", r"estim(?:ate|ates|ated|ating|ation|ations)?"),
    ("classify", r"classif(?:y|ies|ied|ying|ication|ications)?"),
    ("detect", r"detect(?:ion|ions|s|ed|ing)?"),
    ("segment", r"segment(?:ation|ations|s|ed|ing)?"),
    ("identify", r"identif(?:y|ies|ied|ying|ication|ications)?"),
    ("recognize", r"recogni[sz](?:e|es|ed|ing|tion|tions)?"),
    ("measure", r"measur(?:e|es|ed|ing|ement|ements)?"),
    ("monitor", r"monitor(?:ing|s|ed)?"),
    ("assess", r"assess(?:ment|ments|es|ed|ing)?"),
    ("analyze", r"analy[sz](?:e|es|ed|ing|is|sis)?"),
    ("reconstruct", r"reconstruct(?:ion|ions|s|ed|ing)?"),
    ("retrieve", r"retriev(?:e|es|ed|ing|al)?"),
    ("count", r"count(?:ing|s|ed)?"),
)
_EN_SCENARIO_TERMS = (
    "field conditions", "greenhouse", "glasshouse", "indoor", "outdoor",
    "high-throughput", "controlled environment", "in situ", "in-vivo",
    "in-vitro", "wild", "field",
)

# Words that never carry meaning for a literature query.
_STOPWORDS = {
    "the", "and", "for", "from", "with", "that", "this", "these", "those",
    "are", "was", "were", "have", "has", "had", "been", "its", "not", "but",
    "can", "may", "also", "using", "used", "use", "based", "via", "into",
    "such", "each", "more", "however", "within", "than", "which", "their",
    "both", "between", "other", "study", "studies", "paper", "papers",
    "results", "result", "research", "approach", "approaches", "method",
    "methods", "review", "survey", "novel", "new", "recent", "toward",
    "towards", "about", "over", "under", "while", "when", "where",
    "how", "what", "why", "all", "any", "some", "most", "many", "very",
    # Generic nouns that survive tokenisation but mean nothing on their own.
    "conditions", "condition", "data", "dataset", "datasets", "accuracy",
    "performance", "system", "systems", "model", "models", "framework",
    "frameworks", "application", "applications", "case", "cases", "work",
    "works", "technique", "techniques", "technology", "technologies",
    "的", "和", "与", "及", "在", "对", "为", "了", "是", "用", "进行", "研究",
    "方法", "基于", "使用", "一种", "我们", "本文", "论文", "综述", "进展",
}

# Linkers that terminate a captured English phrase.
_EN_LINKERS = {
    "to", "for", "in", "on", "of", "and", "or", "from", "that", "which",
    "while", "under", "over", "with", "by", "a", "an", "the", "is", "are",
    "be", "as", "at", "into", "onto", "than", "then",
}

# Providers whose query syntax we can actually improve on.
PLANNED_SOURCES = ("semantic_scholar", "openalex", "arxiv", "crossref")

_CJK_CLASS = "\u4e00-\u9fff"

TOPIC_ALIASES = {
    "深度学习": "deep learning", "机器学习": "machine learning",
    "计算机视觉": "computer vision", "强化学习": "reinforcement learning",
    "自然语言处理": "natural language processing", "情感分析": "sentiment analysis",
    "情绪识别": "emotion recognition", "多模态": "multimodal",
}


def database_topic(topic: str) -> str:
    """Map exact known topics only; unknown research phrases remain verbatim."""
    return TOPIC_ALIASES.get(topic.strip(), topic)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ResearchIntent:
    """Structured view of a research direction, with explicit uncertainty."""

    topic: str
    direction: str
    language: str = "en"
    object_terms: list[str] = field(default_factory=list)
    method_terms: list[str] = field(default_factory=list)
    task_terms: list[str] = field(default_factory=list)
    scenario_terms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    confidence: float = 0.0
    unparsed: str = ""
    warnings: list[str] = field(default_factory=list)

    def filled_slots(self) -> int:
        return sum(
            1 for slot in (self.object_terms, self.method_terms, self.task_terms, self.scenario_terms)
            if slot
        )

    def describe(self) -> list[str]:
        """Human-readable slot breakdown; omits slots it could not fill."""
        labels = [
            ("研究对象 / object", self.object_terms),
            ("方法 / method", self.method_terms),
            ("任务 / task", self.task_terms),
            ("场景 / scenario", self.scenario_terms),
        ]
        return [f"{label}: {', '.join(terms)}" for label, terms in labels if terms]

    def slot_dict(self) -> dict[str, list[str]]:
        return {
            "object": list(self.object_terms),
            "method": list(self.method_terms),
            "task": list(self.task_terms),
            "scenario": list(self.scenario_terms),
        }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _detect_language(text: str) -> str:
    zh = len(re.findall(f"[{_CJK_CLASS}]", text))
    en = len(re.findall(r"[a-zA-Z]{3,}", text))
    if zh and en:
        return "mixed"
    return "zh" if zh > en else "en"


def _strip_edges(fragment: str) -> str:
    """Trim punctuation and Chinese particles from both ends."""
    fragment = fragment.strip(_ZH_BREAK + " ")
    while fragment and fragment[0] in _ZH_PARTICLES:
        fragment = fragment[1:]
    while fragment and fragment[-1] in _ZH_PARTICLES:
        fragment = fragment[:-1]
    return fragment.strip()


def _add(terms: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in terms:
        terms.append(value)


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < s_end and s_start < end for s_start, s_end in spans)


def _is_cjk(term: str) -> bool:
    return bool(re.fullmatch(f"[{_CJK_CLASS}]+", term))


def _usable_term(term: str) -> bool:
    """Reject fragments that would make a query expression look precise but be junk."""
    term = term.strip().strip('"')
    if not term:
        return False
    if _is_cjk(term):
        return len(term) >= 2
    if re.fullmatch(r"[a-zA-Z\-]+", term):
        return len(term) >= 3 and term.lower() not in _STOPWORDS
    return len(term) >= 2


# ---------------------------------------------------------------------------
# Chinese parsing
# ---------------------------------------------------------------------------


def _zh_cue_spans(text: str, cues: tuple[str, ...]) -> list[tuple[int, int, str]]:
    """Earliest-first, non-overlapping cue occurrences."""
    found: list[tuple[int, int, str]] = []
    for cue in cues:
        for match in re.finditer(re.escape(cue), text):
            found.append((match.start(), match.end(), cue))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str]] = []
    for span in found:
        if _overlaps((span[0], span[1]), [(s, e) for s, e, _ in kept]):
            continue
        kept.append(span)
    return kept


def _zh_tail(
    text: str,
    pos: int,
    boundary_cues: tuple[str, ...],
    max_len: int = 14,
    stop_chars: str = "",
) -> tuple[str, int]:
    """Text after ``pos`` up to the next cue, punctuation or length limit."""
    end = pos
    limit = min(len(text), pos + max_len)
    while end < limit:
        if text[end] in _ZH_BREAK or text[end] in stop_chars:
            break
        if any(text.startswith(cue, end) for cue in boundary_cues):
            break
        end += 1
    return _strip_edges(text[pos:end]), end


def _parse_zh(text: str) -> dict[str, list[str]]:
    method: list[str] = []
    task: list[str] = []
    scenario: list[str] = []
    object_terms: list[str] = []
    claimed: list[tuple[int, int]] = []

    # Scenario cues are standalone markers — record and consume them first.
    for start, end, cue in _zh_cue_spans(text, _ZH_SCENARIO_CUES):
        _add(scenario, cue)
        claimed.append((start, end))

    # Method: "使用/基于 X <task-verb> ..." — X stops at the next cue. It also
    # stops at 的/和/与, because "基于遥感影像的作物病害识别" means method=遥感影像
    # and object=作物病害, not a single six-character method.
    method_boundaries = _ZH_TASK_CUES + _ZH_SCENARIO_CUES + _ZH_METHOD_CUES
    for start, end, _cue in _zh_cue_spans(text, _ZH_METHOD_CUES):
        segment, seg_end = _zh_tail(text, end, method_boundaries, stop_chars="的地得和与及")
        claimed.append((start, max(end, seg_end)))
        if len(segment) >= 2:
            _add(method, segment)

    # Task: the verb itself is the task; what follows it is usually the object.
    for start, end, cue in _zh_cue_spans(text, _ZH_TASK_CUES):
        _add(task, cue)
        segment, seg_end = _zh_tail(text, end, _ZH_TASK_CUES + _ZH_SCENARIO_CUES)
        if len(segment) >= 2:
            _add(object_terms, segment)
            claimed.append((start, seg_end))
            continue
        # Nothing after the verb: the object sits between the previous slot and it.
        previous_end = 0
        for c_start, c_end in claimed:
            if c_start < start:
                previous_end = max(previous_end, c_end)
        before = _strip_edges(text[previous_end:start])
        if len(before) >= 2:
            _add(object_terms, before)
        claimed.append((start, end))

    leftover = _leftover_zh(text, claimed, method + task + scenario + object_terms)
    if not object_terms and leftover:
        object_terms = leftover[:2]
        leftover = leftover[2:]

    return {
        "object": object_terms,
        "method": method,
        "task": task,
        "scenario": scenario,
        "leftover": leftover,
    }


def _leftover_zh(
    text: str,
    claimed: list[tuple[int, int]],
    placed: list[str],
) -> list[str]:
    """Content fragments the rules could not attribute to a slot."""
    chars = list(text)
    for start, end in claimed:
        for index in range(start, min(end, len(chars))):
            chars[index] = " "
    remainder = "".join(chars)
    fragments: list[str] = []
    for raw in re.split(f"[{re.escape(_ZH_BREAK)}]", remainder):
        fragment = _strip_edges(raw)
        if len(fragment) < 2:
            continue
        if fragment in _STOPWORDS or fragment in placed or fragment in fragments:
            continue
        fragments.append(fragment)
    english = [
        word for word in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", remainder.lower())
        if word not in _STOPWORDS and word not in placed and word not in fragments
    ]
    return fragments + english


# ---------------------------------------------------------------------------
# English parsing
# ---------------------------------------------------------------------------


def _en_phrase_spans(lowered: str, phrases: tuple[str, ...]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for phrase in phrases:
        pattern = r"(?<![a-z])" + re.escape(phrase) + r"s?(?![a-z])"
        for match in re.finditer(pattern, lowered):
            spans.append((match.start(), match.end(), phrase))
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str]] = []
    for span in spans:
        if _overlaps((span[0], span[1]), [(s, e) for s, e, _ in kept]):
            continue
        kept.append(span)
    return kept


def _clean_en_phrase(raw: str) -> str:
    """Drop linkers, stopwords and short tokens from a captured phrase."""
    tokens: list[str] = []
    for token in raw.split():
        if token in _EN_LINKERS:
            break
        if len(token) < 3 or token in _STOPWORDS:
            continue
        tokens.append(token)
    while tokens and tokens[-1] in {
        "conditions", "condition", "accuracy", "performance", "data",
        "dataset", "datasets", "results", "result", "system", "systems",
    }:
        tokens.pop()
    return " ".join(tokens[:3])


def _parse_en(text: str) -> dict[str, list[str]]:
    lowered = text.lower()
    method: list[str] = []
    task: list[str] = []
    scenario: list[str] = []
    claimed: list[tuple[int, int]] = []

    for start, end, phrase in _en_phrase_spans(lowered, _EN_METHOD_PHRASES):
        _add(method, phrase)
        claimed.append((start, end))

    # Introducer patterns come before single-word vocabulary so that
    # "using UAV imagery" stays one method phrase instead of splitting into
    # method="uav" plus a stray "imagery" the parser then calls an object.
    for introducer in _EN_METHOD_INTRO:
        pattern = r"(?<![a-z])" + re.escape(introducer) + r"\s+((?:[a-z][a-z\-]{2,}\s*){1,3})"
        for match in re.finditer(pattern, lowered):
            phrase = _clean_en_phrase(match.group(1))
            if not phrase:
                continue
            if _overlaps(match.span(1), claimed):
                continue
            _add(method, phrase)
            claimed.append(match.span(1))

    for word in _EN_METHOD_WORDS:
        pattern = r"(?<![a-z])" + re.escape(word) + r"s?(?![a-z])"
        for match in re.finditer(pattern, lowered):
            if _overlaps(match.span(), claimed):
                continue
            _add(method, word)
            claimed.append(match.span())

    for lemma, pattern in _EN_TASK_STEMS:
        for match in re.finditer(r"(?<![a-z])" + pattern + r"(?![a-z])", lowered):
            _add(task, lemma)
            claimed.append(match.span())

    for term in _EN_SCENARIO_TERMS:
        pattern = r"(?<![a-z])" + re.escape(term) + r"s?(?![a-z])"
        for match in re.finditer(pattern, lowered):
            if _overlaps(match.span(), claimed):
                continue
            _add(scenario, term)
            claimed.append(match.span())

    object_terms, leftover = _en_object_and_leftover(lowered, claimed)

    return {
        "object": object_terms,
        "method": method,
        "task": task,
        "scenario": scenario,
        "leftover": leftover,
    }


def _en_object_and_leftover(
    lowered: str,
    claimed: list[tuple[int, int]],
) -> tuple[list[str], list[str]]:
    """Group unclaimed content words into runs: adjacent words form one phrase."""
    runs: list[list[str]] = []
    current: list[str] = []
    last_end = None
    for match in re.finditer(r"[a-zA-Z][a-zA-Z\-]{2,}", lowered):
        word = match.group(0)
        if word in _STOPWORDS or word in _EN_LINKERS:
            last_end = match.end()
            if current:
                runs.append(current)
                current = []
            continue
        if _overlaps(match.span(), claimed):
            last_end = match.end()
            if current:
                runs.append(current)
                current = []
            continue
        if last_end is not None and match.start() - last_end > 1:
            if current:
                runs.append(current)
            current = []
        current.append(word)
        last_end = match.end()
    if current:
        runs.append(current)

    phrases = [" ".join(run) for run in runs if run]
    # A run is only an "object" if it is not a bare generic word; short or
    # generic runs stay in leftover so they never masquerade as a concept.
    object_terms = [phrase for phrase in phrases if _usable_term(phrase)][:4]
    leftover = [phrase for phrase in phrases if phrase not in object_terms]
    return object_terms, leftover


# ---------------------------------------------------------------------------
# Public: parsing
# ---------------------------------------------------------------------------


def parse_intent(topic: str, direction: str = "") -> ResearchIntent:
    """Split a research direction into object / method / task / scenario slots.

    Rule-based only. Slots hold either a term taken verbatim from the input or
    nothing at all. ``confidence`` combines how many slots were filled with how
    much of the input actually landed in a slot; ``unparsed`` keeps the rest so
    the caller can fall back to raw keywords instead of trusting a bad parse.
    """
    text = (direction or topic or "").strip()
    language = _detect_language(text)

    if not text:
        intent = ResearchIntent(topic=topic, direction=direction, language="en")
        intent.warnings.append("输入为空，无法解析研究意图。Empty input.")
        return intent

    slots = {"object": [], "method": [], "task": [], "scenario": [], "leftover": []}
    if language in ("zh", "mixed"):
        parsed = _parse_zh(text)
        for key, value in parsed.items():
            slots[key].extend(value)
    if language in ("en", "mixed"):
        parsed = _parse_en(text)
        for key, value in parsed.items():
            for item in value:
                if item not in slots[key]:
                    slots[key].append(item)

    # Object first: it is the anchor of the query.
    keywords: list[str] = []
    for key in ("object", "method", "task", "scenario", "leftover"):
        for term in slots[key]:
            if term not in keywords:
                keywords.append(term)

    placed = sum(len(slots[key]) for key in ("object", "method", "task", "scenario"))
    leftover_terms = slots["leftover"]

    intent = ResearchIntent(
        topic=topic,
        direction=direction,
        language=language,
        object_terms=slots["object"],
        method_terms=slots["method"],
        task_terms=slots["task"],
        scenario_terms=slots["scenario"],
        keywords=keywords,
        unparsed=" ".join(leftover_terms),
    )

    warnings: list[str] = []
    if not intent.object_terms:
        warnings.append("未识别到研究对象 / no object recovered.")
    if not intent.method_terms:
        warnings.append("未识别到方法线索 / no method cue found.")
    if not intent.task_terms:
        warnings.append("未识别到任务动词 / no task verb found.")
    if not intent.scenario_terms:
        warnings.append("未识别到场景限定 / no scenario found.")
    if language == "mixed":
        warnings.append("中英混合输入，各库检索式可能跨语言不匹配 / mixed-language input.")
    intent.warnings = warnings

    slot_score = intent.filled_slots() / 4
    coverage = placed / (placed + len(leftover_terms)) if (placed + len(leftover_terms)) else 0.0
    intent.confidence = round(min(0.95, 0.6 * slot_score + 0.4 * coverage), 2)
    return intent


# ---------------------------------------------------------------------------
# Public: query planning
# ---------------------------------------------------------------------------


def _quoted(term: str) -> str:
    return f'"{term}"' if " " in term and not _is_cjk(term) else term


def _slot_clause(terms: list[str], fields: tuple[str, ...] = ("abs", "ti")) -> str:
    """``(abs:X OR ti:X OR abs:Y OR ti:Y)`` for one concept group."""
    parts = [f"{f}:{_quoted(term)}" for term in terms for f in fields]
    return "(" + " OR ".join(parts) + ")"


def build_query_plan(
    intent: ResearchIntent,
    year_from: int,
    year_to: int,
) -> dict[str, dict]:
    """Generate a per-provider query, each in that provider's own syntax."""
    keywords = intent.keywords
    flat = " ".join(keywords)
    raw = (intent.direction or intent.topic or "").strip()

    groups: list[tuple[str, list[str]]] = [
        ("method", [t for t in intent.method_terms if _usable_term(t)]),
        ("task", [t for t in intent.task_terms if _usable_term(t)]),
        ("object", [t for t in intent.object_terms if _usable_term(t)]),
        ("scenario", [t for t in intent.scenario_terms if _usable_term(t)]),
    ]
    usable = [(name, terms) for name, terms in groups if terms]

    if len(usable) >= 2:
        arxiv_expression = " AND ".join(_slot_clause(terms) for _name, terms in usable)
        arxiv_note = (
            "字段限定表达式："
            + "、".join(name for name, _ in usable)
            + " 分别在标题或摘要中匹配。"
        )
    elif flat:
        arxiv_expression = f"all:({flat})"
        arxiv_note = (
            "有效概念分组不足（"
            + str(len(usable))
            + "/4），退化为全字段检索；不做伪精确的字段限定。"
            "Insufficient concept groups: plain all-field search."
        )
    else:
        arxiv_expression = ""
        arxiv_note = "无可用检索词 / no usable query terms."

    return {
        "semantic_scholar": {
            "query": flat or raw,
            "year_from": year_from,
            "year_to": year_to,
            "note": "关键词查询（来自意图槽位），由 S2 自行做相关性排序。Keyword query from intent slots.",
        },
        "openalex": {
            "query": flat or raw,
            "filter": f"publication_year:{year_from}-{year_to}",
            "note": "search= 覆盖标题/摘要/全文；年份用 filter 限定。",
        },
        "arxiv": {
            "query": arxiv_expression,
            "date_range": f"submittedDate:[{year_from}01010000 TO {year_to}12312359]",
            "note": arxiv_note,
        },
        "crossref": {
            "query": flat or raw,
            "note": "选择 Crossref 时检索出版元数据；摘要可能缺失，不代表全文检索。Metadata search when selected; abstracts may be unavailable.",
        },
    }


def plan_from_text(
    topic: str, direction: str, year_from: int, year_to: int
) -> tuple[ResearchIntent, dict]:
    """Convenience: parse then plan in one call."""
    intent = parse_intent(topic, direction)
    return intent, build_query_plan(intent, year_from, year_to)


def _plain_plan(query: str, year_from: int, year_to: int, reason: str) -> dict:
    """Every provider gets the same string — the honest fallback."""
    return {
        "semantic_scholar": {
            "query": query, "year_from": year_from, "year_to": year_to,
            "note": reason,
        },
        "openalex": {
            "query": query,
            "filter": f"publication_year:{year_from}-{year_to}",
            "note": reason,
        },
        "arxiv": {
            "query": f"all:({query})",
            "date_range": f"submittedDate:[{year_from}01010000 TO {year_to}12312359]",
            "note": reason,
        },
        # Crossref uses metadata queries when explicitly selected by the caller.
        "crossref": {
            "query": query,
            "note": f"{reason} 选择 Crossref 时检索出版元数据；摘要可能缺失。Metadata search when selected.",
        },
    }


def plan_payload(topic: str, direction: str, year_from: int, year_to: int) -> dict:
    """Serializable bundle: parsed slots, per-provider strings, and caveats.

    One guard matters more than the rest: if the research question is written in
    a different language than the search keywords, the parsed slots must not
    become the retrieval strings. A Chinese direction parsed into Chinese
    keywords and sent to English-language databases would retrieve worse than
    the plain topic the user typed, so in that case we keep the topic and say so.
    """
    intent = parse_intent(topic, direction)
    payload = {
        "topic": topic,
        "direction": direction,
        "language": intent.language,
        "confidence": intent.confidence,
        "slots": intent.slot_dict(),
        "keywords": list(intent.keywords),
        "unparsed": intent.unparsed,
        "warnings": list(intent.warnings),
        "year_from": year_from,
        "year_to": year_to,
        "degraded": False,
    }

    search_topic = database_topic(topic)
    if search_topic != topic:
        payload["query_normalization"] = {"original": topic, "keywords": search_topic, "rule": "exact_topic_alias"}
        payload["warnings"].append(f"常用主题词映射为 {search_topic}；原研究方向保留，英文检索词可在主题栏手动调整。Exact topic alias, not sentence translation.")
    topic_language = _detect_language(search_topic)
    mismatch = (
        search_topic not in ("", direction)
        and topic_language != "mixed"
        and intent.language != "mixed"
        and topic_language != intent.language
    )
    fallback = (direction or topic or "").strip() or topic
    if mismatch:
        reason = (
            "研究问题语言与检索关键词语言不一致，检索式沿用检索关键词，"
            "仅保留意图解析结果作为记录。Language mismatch between the "
            "research question and the search keywords; the keywords were kept."
        )
        payload["queries"] = _plain_plan(search_topic, year_from, year_to, reason)
        payload["degraded"] = True
        payload["warnings"].append(reason)
        return payload

    if not intent.keywords:
        reason = "意图解析未得到可用检索词，沿用原始检索式。No usable terms parsed."
        payload["queries"] = _plain_plan(fallback, year_from, year_to, reason)
        payload["degraded"] = True
        payload["warnings"].append(reason)
        return payload

    payload["queries"] = build_query_plan(intent, year_from, year_to)
    return payload


def describe_plan(plan: dict) -> list[str]:
    """Flat, human-readable rendering of the generated search strings."""
    lines = []
    for source in PLANNED_SOURCES:
        entry = plan.get(source)
        if not entry or not entry.get("query"):
            continue
        lines.append(f"**{source}** — `{entry['query']}`")
        if entry.get("filter"):
            lines.append(f"  - filter: `{entry['filter']}`")
        if entry.get("date_range"):
            lines.append(f"  - range: `{entry['date_range']}`")
        if entry.get("note"):
            lines.append(f"  - {entry['note']}")
    return lines
