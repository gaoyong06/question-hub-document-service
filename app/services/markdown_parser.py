"""
Markdown解析服务
将Markdown内容解析为题目结构
"""
import re
from typing import List, Optional
from loguru import logger

from app.models import QuestionResult


class MarkdownParser:
    """Markdown解析器，从Markdown中提取题目"""
    
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

