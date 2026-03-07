"""
试卷结构解析公共逻辑
供 document_parser（Word 段落流）与 markdown_parser（Markdown 文本）复用。
支持：试卷标题、注意事项、大题、小题、参考答案（可在文末或中间任意位置）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from app.models import QuestionResult

# Markdown 结构块类型：用于流式解析时传递格式信息。(text, block_type)
# block_type: "h1" 试卷标题, "h2"/"h3" 大题层级, "paragraph" 普通段落, "unordered_list" 无序列表
StructureBlockType = str  # "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "paragraph" | "unordered_list"
StructureBlock = Tuple[str, StructureBlockType]

# 试题类型：与 question-hub-service 表 question.type 及前端约定一致
QUESTION_TYPES = ("single-choice", "multiple-choice", "fill-blank", "judge", "essay")

REFERENCE_ANSWER_HEADERS = ("参考答案", "答案", "标准答案")
SECTION_HEADER_PATTERN = re.compile(
    r"^([一二三四五六七八九十]+)[\.．、]\s*(.+)$",
    re.MULTILINE,
)
# 题号支持可选的转义点号（1\. 2\.），与 document_parser 输出的「1\. 」一致，避免被 Markdown 解析为有序列表
QUESTION_START_PATTERN = re.compile(r"^\d+\\?[\.．、]\s*", re.MULTILINE)

SECTION_RELAXED_KEYWORDS = (
    "选择题", "单选题", "多选题", "填空题", "判断题", "解答题", "计算题", "作图题",
    "第一部分", "第一节", "第二部分", "第二节", "第三部分", "第三节", "第四节", "第五节",
)
SECTION_RELAXED_PATTERN = re.compile(
    r"^(?:第[一二三四五六七八九十\d]+[部分节]\s*)?(?:选择题|单选题|多选题|填空题|判断题|解答题|计算题|作图题).*$"
)
SECTION_TITLE_MAX_LEN = 80

# 注意：不要使用 A/B/C/D 作为拆分点，否则选择题的选项会被错误拆成独立小题。
# A. B. C. D. 是选项标记，应保留在题干+选项的完整内容中，由 parse_stem_and_options_from_choice_content 提取。
QUESTION_SPLIT_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?=\d+\\?[\.．、]\s)", re.MULTILINE),
    re.compile(r"(?:^|\n)\s*(?=[①②③④⑤⑥⑦⑧⑨⑩])", re.MULTILINE),
    re.compile(r"(?:^|\n)\s*(?=\(\d+\)\s)", re.MULTILINE),
]

GRADE_PATTERNS = [
    (re.compile(r"初一|七年级|初中一"), 7),
    (re.compile(r"初二|八年级|初中二"), 8),
    (re.compile(r"初三|九年级|初中三"), 9),
    (re.compile(r"一年级|小学一"), 1),
    (re.compile(r"二年级|小学二"), 2),
    (re.compile(r"三年级|小学三"), 3),
    (re.compile(r"四年级|小学四"), 4),
    (re.compile(r"五年级|小学五"), 5),
    (re.compile(r"六年级|小学六"), 6),
]
SUBJECT_KEYWORDS = ("数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "道德与法治", "科学")


def is_relaxed_section_heading(line: str) -> bool:
    """宽松判断：该行是否像大题标题。"""
    p = (line or "").strip()
    if not p or len(p) > SECTION_TITLE_MAX_LEN:
        return False
    if SECTION_HEADER_PATTERN.match(p):
        return True
    if SECTION_RELAXED_PATTERN.match(p):
        return True
    return any(k in p for k in SECTION_RELAXED_KEYWORDS) and len(p) <= SECTION_TITLE_MAX_LEN


def parse_reference_answers(full_text: str) -> List[str]:
    """从「参考答案」区块文本解析出按题号顺序的答案列表。"""
    out: List[str] = []
    idx = full_text.find("参考答案")
    if idx == -1:
        idx = full_text.find("标准答案")
    if idx == -1:
        idx = full_text.find("答案")
    if idx == -1:
        return out
    header = "参考答案" if full_text.find("参考答案") == idx else ("标准答案" if full_text.find("标准答案") == idx else "答案")
    block = full_text[idx:]
    first_nl = block.find("\n")
    if first_nl != -1:
        first_line = block[:first_nl]
        rest_first = first_line.replace(header, "").strip()
        if not rest_first or not re.search(r"\d+\\?[\.．、]", rest_first):
            block = block[first_nl + 1:]
    else:
        block = re.sub(r"^[\s\S]*?" + re.escape(header), "", block, count=1).strip()
    # 参考答案区块中可能含「一.选择题」「二.判断题」或「## 二.判断题」等大题标题，需从答案中剔除
    section_header_or_next = re.compile(
        r"\n\s*(?:#+\s*)?[一二三四五六七八九十]+[\.．、]\s*(?:选择题|单选题|多选题|填空题|判断题|解答题|计算题|作图题).*$",
        re.MULTILINE,
    )
    pattern = re.compile(r"\d+\\?[\.．、]\s*")
    parts = pattern.split(block)
    for seg in parts:
        ans = re.sub(r"^[\s\u3000]+|[\s\u3000]+$", "", seg)
        if not ans:
            continue
        # 若答案后紧跟下一大题标题（如 "C  \n二.判断题"），只保留答案部分
        ans = section_header_or_next.sub("", ans).strip()
        if not ans:
            continue
        if SECTION_HEADER_PATTERN.match(ans) or re.match(r"^[一二三四五六七八九十]+[\.．、]\s*[选判填计作解].*$", ans):
            continue
        out.append(ans)
    return out


def _find_question_number_splits(block: str) -> Optional[List[int]]:
    """
    基于序号解析主小题拆分点。
    - 嵌套结构(1,1,2,3,2)：序号回退时拆分，即 N < 已出现最大序号
    - 扁平结构(1,2)：无重复时，遇到下一主小题号时拆分
    Returns:
        None: 题号不足 2 个，无法应用本逻辑
        []: 题号足够但无需拆分（单题含子项，如 1,1,2,3）
        [pos,...]: 拆分点位置列表
    """
    # 匹配行首的题号：支持 1. 1\. 1． 1、
    pattern = re.compile(r"(?:^|\n)\s*(\d+)\\?[\.．、]\s*", re.MULTILINE)
    matches = list(pattern.finditer(block))
    if len(matches) < 2:
        return None

    split_positions: List[int] = []
    max_seen = 0
    seen_numbers: set[int] = set()
    has_duplicate = False

    for i, m in enumerate(matches):
        n = int(m.group(1))
        if i == 0:
            max_seen = n
            seen_numbers.add(n)
            continue

        should_split = False
        if n < max_seen:
            should_split = True  # 序号回退，新主小题
        elif n == max_seen + 1 and not has_duplicate:
            should_split = True  # 扁平结构，下一主小题

        if n in seen_numbers:
            has_duplicate = True
        seen_numbers.add(n)
        max_seen = max(max_seen, n)

        if should_split:
            split_positions.append(m.start())

    return split_positions


def split_section_content_into_questions(block: str) -> List[str]:
    """将一大题下的整块文本拆成多道小题。优先使用序号感知拆分，否则回退到原有逻辑。"""
    block = (block or "").strip()
    if not block:
        return []

    result = _find_question_number_splits(block)
    if result is not None:
        # 题号足够：result=[] 表示单题含子项，result=[pos,...] 表示有拆分点
        if not result:
            return [block]  # 无需拆分，整块为一题
        parts = []
        start = 0
        for pos in result:
            part = block[start:pos].strip()
            if part:
                parts.append(part)
            start = pos
        part = block[start:].strip()
        if part:
            parts.append(part)
        if len(parts) >= 2:
            return parts

    # 回退：题号不足 2 个，或 ①②③、(1) 等格式
    for pattern in QUESTION_SPLIT_PATTERNS:
        parts = pattern.split(block)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            return parts
    parts = re.split(r"\n\s*\n", block)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) >= 2:
        return parts
    parts = [p.strip() for p in block.split("\n") if p.strip()]
    if len(parts) >= 2:
        return parts
    return [block]


def section_title_to_question_type(section_title: str) -> str:
    """根据大题标题映射为系统题型。"""
    if not section_title:
        return QUESTION_TYPES[2]
    if "选择题" in section_title or "单选题" in section_title:
        return QUESTION_TYPES[0]
    if "多选题" in section_title:
        return QUESTION_TYPES[1]
    if "判断题" in section_title:
        return QUESTION_TYPES[3]
    if "解答题" in section_title or "作图题" in section_title:
        return QUESTION_TYPES[4]
    if "计算题" in section_title:
        return QUESTION_TYPES[2]
    return QUESTION_TYPES[2]


def to_markdown_line_breaks(text: str) -> str:
    """将单换行转为 Markdown 行尾换行。"""
    if not text or "\n" not in text:
        return text or ""
    return re.sub(r"(?<!\n)(?<!  )\n(?!\n)", "  \n", text)


def protect_fill_blank_underscores(text: str) -> str:
    """
    保护填空题中的下划线填空线（如 _______），避免被 Markdown 解析为斜体而丢失。
    将 3 个及以上连续下划线转义为 \\_，确保渲染时正确显示。
    """
    if not text or "_" not in text:
        return text or ""
    return re.sub(r"_{3,}", lambda m: "\\_" * len(m.group()), text)


def content_has_choice_options(content: str) -> bool:
    """题干是否包含 A. B. C. D. 选项形式。"""
    if not content or len(content) < 4:
        return False
    option_markers = re.findall(r"[A-D]\\?[\.．、]\s*", content)
    return len(option_markers) >= 2


def parse_stem_and_options_from_choice_content(content: str) -> Tuple[str, List[str]]:
    """从「题干 + A. B. C. D.」形式拆出题干与选项列表。"""
    if not content:
        return "", []
    parts = re.split(r"(?:\n|^)\s*[A-D]\\?[\.．、]\s*", content, maxsplit=0)
    stem = (parts[0].strip() if parts else "").strip()
    options = [p.strip() for p in parts[1:] if p.strip()]
    return stem, options


def parse_grade_subject(document_title: str, full_text: str) -> Tuple[int, str]:
    """从文档标题及正文前段识别年级、学科。"""
    text = ((document_title or "") + "\n" + (full_text or "")[:800]).strip()
    grade = 0
    subject = ""
    for pattern, g in GRADE_PATTERNS:
        if pattern.search(text):
            grade = g
            break
    for kw in SUBJECT_KEYWORDS:
        if kw in text:
            subject = kw
            break
    return grade, subject


def apply_grade_subject_to_questions(
    questions: List[QuestionResult], document_grade: int, document_subject: str
) -> None:
    """将年级、学科回填到题目列表。"""
    if not questions:
        return
    for q in questions:
        if 1 <= document_grade <= 9:
            q.grade = document_grade
        if (document_subject or "").strip():
            q.subject = document_subject.strip()


@dataclass
class ParsedStructure:
    """流式解析得到的试卷结构。"""
    document_title: str = ""
    document_description: str = ""
    sections: List[Tuple[int, str, List[str]]] = field(default_factory=list)
    answer_block_texts: List[str] = field(default_factory=list)


def parse_structure_from_blocks(blocks: List[StructureBlock]) -> ParsedStructure:
    """
    基于 Markdown 结构块流式解析试卷结构。
    利用块类型（#/##/列表等）明确区分：试卷标题(h1)、大题(h2/h3)、普通段落、列表。
    """
    order_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    next_order = 11
    implicit_section_order = 1
    structure = ParsedStructure()
    current_section: Optional[Tuple[int, str]] = None
    current_question_lines: List[str] = []
    title_done = False
    in_notes = False
    notes_lines: List[str] = []
    in_answer_block = False
    answer_block_lines: List[str] = []

    def flush_section() -> None:
        nonlocal current_section, current_question_lines
        if not current_section:
            return
        block = "\n".join(current_question_lines).strip()
        section_questions = split_section_content_into_questions(block) if block else []
        structure.sections.append((current_section[0], current_section[1], section_questions))
        current_section = None
        current_question_lines = []

    def flush_answer_block() -> None:
        nonlocal in_answer_block, answer_block_lines
        if answer_block_lines:
            structure.answer_block_texts.append("\n".join(answer_block_lines))
        answer_block_lines = []
        in_answer_block = False

    for text, block_type in blocks:
        p = (text or "").strip()
        if not p:
            if in_answer_block:
                answer_block_lines.append("")
            elif current_section and current_question_lines:
                current_question_lines.append("")
            elif in_notes:
                notes_lines.append("")
            continue

        # 1. 参考答案区块（按内容识别，与格式无关）
        if any(h in p for h in REFERENCE_ANSWER_HEADERS) and (
            p.startswith("参考答案") or p.startswith("标准答案") or (p.startswith("答案") and len(p) <= 10)
        ):
            flush_section()
            if in_answer_block:
                flush_answer_block()
            in_answer_block = True
            answer_block_lines = [p] if p else []
            continue

        if in_answer_block:
            answer_block_lines.append(p)
            continue

        # 2. 注意事项
        if p.startswith("注意事项"):
            in_notes = True
            start = len("注意事项")
            if start < len(p) and p[start] in "：:":
                start += 1
            rest = p[start:].strip()
            if rest:
                notes_lines.append(rest)
            continue
        if in_notes:
            # 格式明确为大题标题（h2/h3 等）时结束注意事项
            if block_type in ("h2", "h3", "h4", "h5", "h6") or SECTION_HEADER_PATTERN.match(p) or is_relaxed_section_heading(p):
                in_notes = False
                structure.document_description = to_markdown_line_breaks("\n".join(notes_lines).strip())
                notes_lines = []
            else:
                notes_lines.append(p)
                continue

        # 3. 根据 Markdown 层级：h1 作试卷标题，h2/h3 作大题
        if block_type == "h1":
            if not title_done and not structure.document_title:
                structure.document_title = p[:200].strip()
                title_done = True
            continue
        if block_type in ("h2", "h3", "h4", "h5", "h6"):
            flush_section()
            section_match = SECTION_HEADER_PATTERN.match(p)
            if section_match:
                cn_num, rest = section_match.group(1), section_match.group(2).strip()
                order = order_map.get(cn_num)
                if order is None:
                    order = next_order
                    next_order += 1
                title = (cn_num + "." + rest) if not rest.startswith(".") else (cn_num + rest)
                current_section = (order, title)
            else:
                current_section = (implicit_section_order, p)
                implicit_section_order += 1
            current_question_lines = []
            continue

        # 4. 无大题时题号行仅作试卷标题候选
        if QUESTION_START_PATTERN.match(p) and not current_section:
            if not title_done and not structure.document_title:
                structure.document_title = p[:200].strip()
                title_done = True
            continue

        # 5. 普通段落/列表：归属当前大题或标题或注意事项
        if current_section:
            current_question_lines.append(p)
        elif not title_done and not in_notes:
            if not structure.document_title:
                structure.document_title = p[:200].strip()
            title_done = True
        elif in_notes:
            notes_lines.append(p)

    if in_notes and notes_lines:
        structure.document_description = to_markdown_line_breaks("\n".join(notes_lines).strip())
    flush_section()
    if in_answer_block:
        flush_answer_block()

    return structure


def parse_structure(
    paragraphs: List[str],
    format_suggests_heading: Callable[[int], bool],
) -> ParsedStructure:
    """
    流式解析试卷结构：试卷标题、注意事项、大题、小题、参考答案（可在任意位置）。
    format_suggests_heading(i) 表示第 i 段是否因格式/层级像大题标题（如 Markdown ####、Word 标题样式）。
    """
    order_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    next_order = 11
    implicit_section_order = 1
    structure = ParsedStructure()
    current_section: Optional[Tuple[int, str]] = None
    current_question_lines: List[str] = []
    title_done = False
    in_notes = False
    notes_lines: List[str] = []
    in_answer_block = False
    answer_block_lines: List[str] = []

    def flush_section() -> None:
        nonlocal current_section, current_question_lines
        if not current_section:
            return
        block = "\n".join(current_question_lines).strip()
        section_questions = split_section_content_into_questions(block) if block else []
        structure.sections.append((current_section[0], current_section[1], section_questions))
        current_section = None
        current_question_lines = []

    def flush_answer_block() -> None:
        nonlocal in_answer_block, answer_block_lines
        if answer_block_lines:
            structure.answer_block_texts.append("\n".join(answer_block_lines))
        answer_block_lines = []
        in_answer_block = False

    for i, para in enumerate(paragraphs):
        p = (para or "").strip()
        if not p:
            if in_answer_block:
                answer_block_lines.append("")
            elif current_section and current_question_lines:
                current_question_lines.append("")
            elif in_notes:
                notes_lines.append("")
            continue

        # 1. 参考答案区块开始（文末或中间均支持）
        if any(h in p for h in REFERENCE_ANSWER_HEADERS) and (
            p.startswith("参考答案") or p.startswith("标准答案") or (p.startswith("答案") and len(p) <= 10)
        ):
            flush_section()
            if in_answer_block:
                flush_answer_block()
            in_answer_block = True
            # 首行保留“参考答案”等，便于 parse_reference_answers 定位
            answer_block_lines = [p] if p else []
            continue

        if in_answer_block:
            # 参考答案区块内常有“一.选择题”“二.判断题”等小节标题，一律当作答案内容收集，不结束区块
            answer_block_lines.append(p)
            continue

        # 2. 注意事项
        if p.startswith("注意事项"):
            in_notes = True
            start = len("注意事项")
            if start < len(p) and p[start] in "：:":
                start += 1
            rest = p[start:].strip()
            if rest:
                notes_lines.append(rest)
            continue
        if in_notes:
            if SECTION_HEADER_PATTERN.match(p) or is_relaxed_section_heading(p) or format_suggests_heading(i):
                in_notes = False
                structure.document_description = to_markdown_line_breaks("\n".join(notes_lines).strip())
                notes_lines = []
            else:
                notes_lines.append(p)
                continue

        # 3. 大题标题
        section_match = SECTION_HEADER_PATTERN.match(p)
        if section_match:
            flush_section()
            cn_num, rest = section_match.group(1), section_match.group(2).strip()
            order = order_map.get(cn_num)
            if order is None:
                order = next_order
                next_order += 1
            title = (cn_num + "." + rest) if not rest.startswith(".") else (cn_num + rest)
            current_section = (order, title)
            current_question_lines = []
            continue
        if is_relaxed_section_heading(p) or format_suggests_heading(i):
            flush_section()
            current_section = (implicit_section_order, p)
            implicit_section_order += 1
            current_question_lines = []
            continue

        # 4. 无大题时的题号行：仅作试卷标题候选
        if QUESTION_START_PATTERN.match(p) and not current_section:
            if not title_done and not structure.document_title:
                structure.document_title = p[:200].strip()
                title_done = True
            continue

        # 5. 归属当前大题或标题
        if current_section:
            current_question_lines.append(p)
        elif not title_done and not in_notes:
            if not structure.document_title:
                structure.document_title = p[:200].strip()
            title_done = True
        elif in_notes:
            notes_lines.append(p)

    if in_notes and notes_lines:
        structure.document_description = to_markdown_line_breaks("\n".join(notes_lines).strip())
    flush_section()
    if in_answer_block:
        flush_answer_block()

    return structure


def structure_to_questions(
    structure: ParsedStructure,
    reference_answers: List[str],
) -> List[QuestionResult]:
    """将解析出的结构映射为题目列表，答案按参考答案顺序回填。"""
    questions: List[QuestionResult] = []
    for section_order, section_title, contents in structure.sections:
        q_type = section_title_to_question_type(section_title)
        for content in contents:
            if not content or not content.strip():
                continue
            content = content.strip()
            options: Optional[List[str]] = None
            stem = content
            if q_type in (QUESTION_TYPES[0], QUESTION_TYPES[1]) and content_has_choice_options(content):
                stem, opts = parse_stem_and_options_from_choice_content(content)
                if len(opts) >= 2:
                    options = opts
            q = QuestionResult(
                type=q_type,
                content=stem,
                options=options,
                answer="",
                difficulty="medium",
                grade=1,
                subject="",
                section_order=section_order,
                section_title=section_title,
            )
            questions.append(q)

    for i, q in enumerate(questions):
        if not (q.answer and q.answer.strip()) and i < len(reference_answers):
            raw = reference_answers[i].strip()
            if q.type == QUESTION_TYPES[3]:
                q.answer = "true" if raw in ("对", "正确", "√", "T", "t") else ("false" if raw in ("错", "错误", "×", "F", "f") else raw)
            else:
                q.answer = raw

    for q in questions:
        if q.content:
            q.content = protect_fill_blank_underscores(to_markdown_line_breaks(q.content))
        if q.answer:
            q.answer = protect_fill_blank_underscores(to_markdown_line_breaks(q.answer))
        if q.options:
            q.options = [protect_fill_blank_underscores(to_markdown_line_breaks(o)) for o in q.options]

    for q in questions:
        if q.type not in QUESTION_TYPES:
            q.type = QUESTION_TYPES[2]
    return questions
