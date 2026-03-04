"""
Markdown解析服务
将Markdown内容解析为题目结构。支持流式解析（与 document_parser 对齐）：
试卷标题、注意事项、大题、小题、参考答案（可在文末或中间任意位置）。
"""
import re
from typing import List, Optional, Tuple
from loguru import logger

from app.models import QuestionResult
from app.services.exam_structure_utils import (
    SECTION_HEADER_PATTERN,
    is_relaxed_section_heading,
    parse_structure,
    structure_to_questions,
    parse_reference_answers,
    parse_grade_subject,
    apply_grade_subject_to_questions,
)


class MarkdownParser:
    """Markdown解析器：流式解析与按题型正则提取两种方式"""

    def parse_markdown_to_exam(
        self,
        markdown_content: str,
    ) -> Tuple[List[QuestionResult], str, str, int, str]:
        """
        将 Markdown 流式解析为试卷结构，返回题目列表与元数据（与 document_parser 输出对齐）。
        Returns:
            (questions, document_title, document_description, document_grade, document_subject)
        """
        logger.info("Parsing markdown to exam structure (stream)")
        paragraphs, heading_hints = self._markdown_to_paragraphs_and_hints(markdown_content)
        if not paragraphs:
            logger.warning("No paragraphs from markdown")
            return [], "", "", 0, ""

        structure = parse_structure(paragraphs, lambda i: heading_hints[i] if i < len(heading_hints) else False)
        answer_block_text = "\n".join(structure.answer_block_texts) if structure.answer_block_texts else ""
        reference_answers = parse_reference_answers(answer_block_text) if answer_block_text else []
        questions = structure_to_questions(structure, reference_answers)
        document_grade, document_subject = parse_grade_subject(structure.document_title, markdown_content)
        apply_grade_subject_to_questions(questions, document_grade, document_subject)
        logger.info(
            "Stream parsed: sections=%s, questions=%s, reference_answers=%s",
            len(structure.sections),
            len(questions),
            len(reference_answers),
        )
        return (
            questions,
            structure.document_title or "",
            structure.document_description or "",
            document_grade,
            document_subject or "",
        )

    def _markdown_to_paragraphs_and_hints(self, markdown_content: str) -> Tuple[List[str], List[bool]]:
        """将 Markdown 按双换行拆成段落，并标记每段是否像大题/标题（用于流式解析）。"""
        blocks = re.split(r"\n\s*\n", markdown_content)
        paragraphs = []
        heading_hints = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            first_line = block.split("\n")[0].strip()
            first_line_stripped = first_line
            while first_line_stripped.startswith("#"):
                first_line_stripped = first_line_stripped.lstrip("#").strip()
            para_text = block
            if block.startswith("#"):
                lines = block.split("\n")
                new_lines = []
                for line in lines:
                    s = line.strip()
                    while s.startswith("#"):
                        s = s.lstrip("#").strip()
                    new_lines.append(s)
                para_text = "\n".join(new_lines).strip()
            paragraphs.append(para_text)
            hint = (
                block.startswith("#")
                or SECTION_HEADER_PATTERN.match(first_line_stripped)
                or is_relaxed_section_heading(first_line_stripped)
            )
            heading_hints.append(hint)
        return paragraphs, heading_hints

    def parse_markdown_to_questions(self, markdown_content: str) -> List[QuestionResult]:
        """
        将Markdown内容解析为题目列表
        
        Args:
            markdown_content: Markdown格式的内容
            
        Returns:
            题目列表
        """
        logger.info("Parsing markdown content to extract questions")
        
        questions = []
        
        # 尝试识别各种题型
        single_choice_questions = self._extract_single_choice_from_markdown(markdown_content)
        questions.extend(single_choice_questions)
        
        multiple_choice_questions = self._extract_multiple_choice_from_markdown(markdown_content)
        questions.extend(multiple_choice_questions)
        
        fill_blank_questions = self._extract_fill_blank_from_markdown(markdown_content)
        questions.extend(fill_blank_questions)
        
        judge_questions = self._extract_judge_from_markdown(markdown_content)
        questions.extend(judge_questions)
        
        essay_questions = self._extract_essay_from_markdown(markdown_content)
        questions.extend(essay_questions)
        
        logger.info(f"Extracted {len(questions)} questions from markdown")
        return questions
    
    def _extract_single_choice_from_markdown(self, content: str) -> List[QuestionResult]:
        """从Markdown中提取单选题"""
        questions = []
        
        # 匹配模式：题目 + A/B/C/D选项 + 答案：X
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、]\s*.+?)\s+(B[\.、]\s*.+?)\s+(C[\.、]\s*.+?)\s+(D[\.、]\s*.+?)\s+答案[：:]\s*([ABCD])'
        
        matches = re.finditer(pattern, content, re.DOTALL | re.MULTILINE)
        
        for match in matches:
            content_text = match.group(1).strip()
            option_a = match.group(2).strip()
            option_b = match.group(3).strip()
            option_c = match.group(4).strip()
            option_d = match.group(5).strip()
            answer = match.group(6).strip()
            
            # 清理选项格式
            option_a = re.sub(r'^A[\.、]\s*', '', option_a)
            option_b = re.sub(r'^B[\.、]\s*', '', option_b)
            option_c = re.sub(r'^C[\.、]\s*', '', option_c)
            option_d = re.sub(r'^D[\.、]\s*', '', option_d)
            
            questions.append(QuestionResult(
                type="single-choice",
                content=content_text,
                options=[option_a, option_b, option_c, option_d],
                answer=answer,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions
    
    def _extract_multiple_choice_from_markdown(self, content: str) -> List[QuestionResult]:
        """从Markdown中提取多选题"""
        questions = []
        
        # 匹配模式：题目 + A/B/C/D选项 + 答案：多个选项（如：AB、ABC等）
        pattern = r'(\d+[\.、]?\s*.+?)\s+(A[\.、]\s*.+?)\s+(B[\.、]\s*.+?)\s+(C[\.、]\s*.+?)\s+(D[\.、]\s*.+?)\s+答案[：:]\s*([ABCD]{2,})'
        
        matches = re.finditer(pattern, content, re.DOTALL | re.MULTILINE)
        
        for match in matches:
            content_text = match.group(1).strip()
            option_a = match.group(2).strip()
            option_b = match.group(3).strip()
            option_c = match.group(4).strip()
            option_d = match.group(5).strip()
            answer = match.group(6).strip()
            
            # 清理选项格式
            option_a = re.sub(r'^A[\.、]\s*', '', option_a)
            option_b = re.sub(r'^B[\.、]\s*', '', option_b)
            option_c = re.sub(r'^C[\.、]\s*', '', option_c)
            option_d = re.sub(r'^D[\.、]\s*', '', option_d)
            
            questions.append(QuestionResult(
                type="multiple-choice",
                content=content_text,
                options=[option_a, option_b, option_c, option_d],
                answer=answer,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions
    
    def _extract_fill_blank_from_markdown(self, content: str) -> List[QuestionResult]:
        """从Markdown中提取填空题"""
        questions = []
        
        # 匹配模式：题目（包含下划线或括号）+ 答案：...
        pattern = r'(\d+[\.、]?\s*.+?[（(].*?[）)]|.+?___.+?)\s+答案[：:]\s*(.+?)(?=\d+[\.、]|$)'
        
        matches = re.finditer(pattern, content, re.DOTALL | re.MULTILINE)
        
        for match in matches:
            content_text = match.group(1).strip()
            answer = match.group(2).strip()
            
            questions.append(QuestionResult(
                type="fill-blank",
                content=content_text,
                answer=answer,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions
    
    def _extract_judge_from_markdown(self, content: str) -> List[QuestionResult]:
        """从Markdown中提取判断题"""
        questions = []
        
        # 匹配模式：题目 + 答案：对/错 或 正确/错误
        pattern = r'(\d+[\.、]?\s*.+?)\s+答案[：:]\s*([对错正确错误√×])'
        
        matches = re.finditer(pattern, content, re.DOTALL | re.MULTILINE)
        
        for match in matches:
            content_text = match.group(1).strip()
            answer_text = match.group(2).strip()
            
            # 转换为标准答案格式
            if answer_text in ["对", "正确", "√"]:
                answer = "true"
            elif answer_text in ["错", "错误", "×"]:
                answer = "false"
            else:
                continue
            
            questions.append(QuestionResult(
                type="judge",
                content=content_text,
                answer=answer,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions
    
    def _extract_essay_from_markdown(self, content: str) -> List[QuestionResult]:
        """从Markdown中提取解答题"""
        questions = []
        
        # 匹配模式：题目 + 解析：...
        pattern = r'(\d+[\.、]?\s*.+?)\s+解析[：:]\s*(.+?)(?=\d+[\.、]|$)'
        
        matches = re.finditer(pattern, content, re.DOTALL | re.MULTILINE)
        
        for match in matches:
            content_text = match.group(1).strip()
            explanation = match.group(2).strip()
            
            questions.append(QuestionResult(
                type="essay",
                content=content_text,
                answer="",  # 解答题没有标准答案
                explanation=explanation,
                difficulty="medium",
                grade=1,
                subject=""
            ))
        
        return questions

