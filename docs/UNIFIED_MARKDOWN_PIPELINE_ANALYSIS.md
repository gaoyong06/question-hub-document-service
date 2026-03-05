# 统一「MarkItDown → 图片处理 → Markdown 流式解析」方案分析

## 目标流程（三步骤）

1. **各种格式 → Markdown**：所有支持格式（含 .doc/.docx）统一经 MarkItDown 转为 markdown。
2. **Markdown 内图片处理**：从 markdown 中提取图片（含 `data:image/png;base64,...`），上传到 asset-service，在 markdown 中用图片 URL 替换。
3. **Markdown → 试卷结构**：用与 document_parser 一致的**流式解析**思路，从 markdown 解析出「试卷标题、注意事项、大题、小题、参考答案」，再映射为题目列表。

---

## 方案是否合理：结论

**合理，且推荐作为长期统一方案。**

| 维度 | 说明 |
|------|------|
| **一致性** | 所有格式走同一条链路，行为一致，便于测试与维护。 |
| **可维护性** | 只维护一套「Markdown 流式解析」逻辑，无需同时维护 document_parser（段落+格式）与 markdown_parser（题型正则）两套。 |
| **扩展性** | 新格式只要 MarkItDown 支持即可接入，无需再写格式专用解析。 |
| **与现有设计对齐** | 你已有 UNIFY_VIA_MARKITDOWN_ANALYSIS.md，结论也是「统一走 MarkItDown 可降低复杂度、提升鲁棒性」；本方案是将其落地为「三步流水线」。 |

**需要注意的点：**

- **Word 上**：当前 python-docx 有「段落 + 字号/加粗/缩进」辅助大题边界；统一后大题边界主要靠 **Markdown 标题层级（`#`/`####`）和文字规则（一. 二.、题型词）**。从你提供的 MarkItDown 输出来看，`#### 一.选择题(共6题，共12分)` 已足够清晰，对这类试卷效果可接受；若遇到「无标题层级、仅靠字号区分」的 docx，可再视情况加回「仅 .docx 可选走 python-docx」的回退。
- **图片**：MarkItDown 对 Word 常输出 `![](data:image/png;base64,...)`。需要在 image_processor 中**支持 data URL**：解码 base64 → 写临时文件 → 上传 → 在 markdown 中替换为 URL。

---

## 三步与现有组件的对应关系

| 步骤 | 现有能力 | 需要新增/调整 |
|------|----------|----------------|
| 1. 格式 → Markdown | MarkItDown 已支持 .doc/.docx 等；consumer 里非 Word 已用 `convert_to_markdown`，Word 也已可选用 MarkItDown 得到 markdown_content | 调整 consumer：**所有格式**都只走「MarkItDown → markdown」一条分支，不再对 Word 单独调 `parser.parse_document`。 |
| 2. 图片上传并替换 | `image_processor.process_images_in_markdown` 已支持 `![](path)`、`![](http(s)://...)`，替换为上传后的 URL | **支持 data URL**：识别 `![](data:image/...;base64,...)`，base64 解码 → 写临时文件 → 上传 → 同逻辑替换为 URL。 |
| 3. Markdown → 试卷 | document_parser 有完整流式解析（标题/注意事项/大题/小题/参考答案）+ 参考答案区按序回填；markdown_parser 目前是按题型正则、且要求题干后跟「答案：X」 | 在 **markdown_parser** 中实现「**流式解析**」：输入为「按行或按块拆开的 markdown」，识别 `##`/`####`、一. 二.、1. 2.、参考答案区块，复用 document_parser 的 `_split_section_content_into_questions`、`_section_title_to_question_type`、`_parse_reference_answers` 等逻辑（可抽到公共模块或复制适配）。输出与 document_parser 一致：题目列表 + document_title + document_description + 年级/学科（可从标题/前文识别）。 |

---

## markdown_parser 流式解析设计要点（对齐 document_parser）

1. **输入**  
   - 将 markdown 按「段落」拆成列表：可先按 `\n\n` 拆，再对每段 strip；或按行扫描，以 `#`/`##`/`####` 或「一. 二.」等为边界。  
   - 若希望与 document_parser 的 `_parse_structure(paragraphs, format_infos)` 尽量一致，可把 markdown 拆成 `paragraphs`，并为每段推导一个「是否像标题」的 format：例如该段是否以 `#`～`######` 开头，或是否匹配 `^[一二三四五六七八九十]+[\.．、]`。

2. **结构识别（与 document_parser 一致）**  
   - **参考答案区块**：某段以「参考答案」「标准答案」或短「答案」开头 → 从该段起至文末为答案区；答案区文本交给 `_parse_reference_answers` 得到有序答案列表。  
   - **注意事项**：某段以「注意事项」开头，直到遇到大题标题或参考答案结束。  
   - **大题标题**：强匹配 `一. 二. …` + 题型，或宽松匹配题型词/「第 X 部分」；若 markdown 里是 `#### 一.选择题(...)`，去掉 `####` 后同样用 SECTION_HEADER_PATTERN / 宽松规则。  
   - **小题**：一大题下的连续内容按「1. 2. 3.」或 a. ①② (1) 或双换行拆（即复用 `_split_section_content_into_questions`）。

3. **题型与答案**  
   - 题型由大题标题决定（`_section_title_to_question_type`）。  
   - 选择题若题干内容含 A. B. C. D.，用 `_parse_stem_and_options_from_choice_content` 拆题干与选项。  
   - 答案统一由「参考答案区」解析出的列表按题序回填，不依赖「题干后跟 答案：X」。

4. **年级/学科**  
   - 与 document_parser 相同：从试卷标题 + 正文前若干字符用 `_parse_grade_subject` 识别，并回填到题目。

5. **输出**  
   - 题目列表 + document_title + document_description + document_grade + document_subject，与当前 `parse_document` 返回值对齐，便于 consumer 统一处理（建卷、写 MQ、落库 markdown_content 等）。

---

## 实现时建议的代码结构

- **公共解析逻辑**：将 document_parser 中「与格式无关」的部分抽到公共模块（如 `app/services/exam_structure_utils.py` 或保留在 document_parser 中由 markdown_parser 调用）：  
  `_parse_reference_answers`、`_split_section_content_into_questions`、`_section_title_to_question_type`、`_content_has_choice_options`、`_parse_stem_and_options_from_choice_content`、`_to_markdown_line_breaks`、`_parse_grade_subject`、`_apply_grade_subject_to_questions`、SECTION_HEADER_PATTERN、REFERENCE_ANSWER_HEADERS、QUESTION_SPLIT_PATTERNS 等。
- **markdown_parser**：新增「从 markdown 文本 → paragraphs（+ 可选标题层级）」的拆分，然后调用与 document_parser 相同的结构解析与 mapping 逻辑，得到题目列表和元数据。
- **image_processor**：在 `extract_images_from_markdown` 或后续处理中识别 `data:image/...;base64,XXX`；解码后写临时文件，再走现有「上传 + replace」流程。

这样**所有格式**都按「1. MarkItDown → 2. 图片上传替换 → 3. markdown 流式解析」执行，效果会与当前 document_parser 对齐，且更统一、后续更好维护。

---

## 小结

- **「各种格式 → MarkItDown → 图片上传替换 → markdown 流式解析」作为统一流程是合理的**，建议按上述三步落地。  
- **markdown_parser** 增强为「流式解析 + 参考答案区回填 + 年级/学科」后，效果可对齐 python-docx，并在所有格式上一致。  
- **图片处理**只需在现有「提取 → 上传 → 替换」链路上增加对 **data URL** 的支持即可。  
- 若希望平滑迁移，可先保留「仅 .docx 可配置走 python-docx」的可选分支，默认走统一 Markdown 流水线，待验证稳定后再下线 document_parser 的 Word 专用分支。

---

## 本地验证图片（data URL）处理

在项目根目录激活 venv 后执行：

```bash
python scripts/test_markdown_images_local.py "/Users/gaoyong/Downloads/题库/一年级-数学/一年级上册数学期末测试卷（达标题）.docx"
```

脚本会：MarkItDown 转 markdown → 提取图片（含 data URL）→ 解码 data URL 为临时文件；若配置了 asset_service_api_base_url 且非 localhost，会执行完整上传并替换，并打印处理后的 markdown 片段。
