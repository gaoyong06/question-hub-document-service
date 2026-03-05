"""
试卷结构解析公共逻辑
供 document_parser（Word 段落流）与 markdown_parser（Markdown 文本）复用。
支持：试卷标题、注意事项、大题、小题、参考答案（可在文末或中间任意位置）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Callable

from app.models import QuestionResult

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

QUESTION_SPLIT_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?=\d+\\?[\.．、]\s)", re.MULTILINE),
    re.compile(r"(?:^|\n)\s*(?=[a-dA-D]\\?[\.．、]\s)", re.MULTILINE),
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
    pattern = re.compile(r"\d+\\?[\.．、]\s*")
    parts = pattern.split(block)
    for seg in parts:
        ans = re.sub(r"^[\s\u3000]+|[\s\u3000]+$", "", seg)
        if not ans:
            continue
        if SECTION_HEADER_PATTERN.match(ans) or re.match(r"^[一二三四五六七八九十]+[\.．、]\s*[选判填计作解].*$", ans):
            continue
        out.append(ans)
    return out


def split_section_content_into_questions(block: str) -> List[str]:
    """将一大题下的整块文本拆成多道小题。"""
    block = (block or "").strip()
    if not block:
        return []
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
    if "解答题" in section_title:
        return QUESTION_TYPES[4]
    return QUESTION_TYPES[2]


def to_markdown_line_breaks(text: str) -> str:
    """将单换行转为 Markdown 行尾换行。"""
    if not text or "\n" not in text:
        return text or ""
    return re.sub(r"(?<!\n)(?<!  )\n(?!\n)", "  \n", text)


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
            first_rest = ""
            for h in ("参考答案", "标准答案", "答案"):
                if p.startswith(h):
                    first_rest = p[len(h):].lstrip("：: \t")
                    break
            in_answer_block = True
            answer_block_lines = [first_rest] if first_rest else []
            continue

        if in_answer_block:
            if SECTION_HEADER_PATTERN.match(p) or is_relaxed_section_heading(p) or format_suggests_heading(i):
                flush_answer_block()
            else:
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
            q.content = to_markdown_line_breaks(q.content)
        if q.answer:
            q.answer = to_markdown_line_breaks(q.answer)
        if q.options:
            q.options = [to_markdown_line_breaks(o) for o in q.options]

    for q in questions:
        if q.type not in QUESTION_TYPES:
            q.type = QUESTION_TYPES[2]
    return questions
