# 是否统一「先转 Markdown（MarkItDown）再解析试卷」— 结合当前场景的分析

## 当前状态简要

- **实际支持的格式**：目前仅 **Word（.docx）** 有完整实现：`parse_document` 校验 `b"PK"` 后直接用 python-docx 解析，注释里提到的「其他格式通过 MarkItDown 转换」**尚未落地**。
- **已迭代的能力**：
  - 流式结构解析（标题 / 注意事项 / 大题 / 小题 / 参考答案）
  - 宽松大题识别（一. 二. + 题型词、第 X 部分/节）
  - 小题多模式拆分（1. / a. / ①② / (1)、双换行、单换行）
  - **Word 格式辅助**：样式、字号、缩进、加粗用于大题/注意事项边界
- **入口**：`parse_document(file_path)` 只接 Word；图片通过 docx 内嵌提取并占位 `{{IMAGE_N}}`。

---

## 方案：统一「先 MarkItDown → Markdown，再解析」

即：**所有格式（含 Word）先经 [MarkItDown](https://github.com/microsoft/markitdown) 转为 Markdown 文本，再只对 Markdown 做一套试卷结构解析。**

### 1. 复杂度

| 维度 | 当前（仅 Word） | 统一走 MarkItDown 后 |
|------|------------------|----------------------|
| **格式分支** | 1 条（docx 专用：doc → paragraphs + format_infos） | 1 条：任意支持格式 → Markdown → 同一套解析 |
| **解析逻辑** | `_parse_structure(paragraphs, format_infos)` 强依赖「段落列表 + 可选格式」 | 可收敛为「仅对 Markdown 的段落/标题流」解析，格式用 `#`/`##` 层级代替 |
| **代码量** | 大量 docx 专用代码（图片从 drawing/pict 抽、格式从 run/paragraph 抽） | 删除或大幅收缩；新增「MarkItDown 调用 + Markdown → paragraphs（+ 标题层级）」的薄适配层 |
| **依赖** | python-docx（仅 Word） | MarkItDown（Word/PDF/PPT/Excel/图片等一套依赖） |

结论：**整体复杂度会降低**——格式解析交给 MarkItDown，我们只维护「Markdown → 试卷结构 → 业务题目」这一条链路；多格式支持变成「能否被 MarkItDown 转」的问题，而不是为每种格式写一套解析。

### 2. 准确度

| 方面 | 说明 |
|------|------|
| **Word** | 当前用 python-docx 拿**原始格式**（字号、缩进、样式、加粗），对「没按一. 二. / 题型词」排版的试卷更准。统一走 MarkItDown 后，大题主要靠 **Markdown 的 `#`/`##`** 和现有**文字规则**（一. 二.、题型词等）；若 Word 里标题被转成 `##`，准确度可接受；若转换结果不区分标题/正文（全成普通段落），会略逊于当前「格式辅助」方案。 |
| **PDF / 其他** | 当前**无实现**；统一后由 MarkItDown 负责转换，**从无到有**，准确度取决于 MarkItDown 对该类文件的表现，通常优于自研一套 PDF 解析。 |
| **参考答案 / 小题拆分** | 仍用现有 `_parse_reference_answers`、`_split_section_content_into_questions` 等逻辑，输入从「docx 段落列表」改为「Markdown 拆成的段落列表」，规则不变，准确度预期一致。 |

结论：**对纯 Word 且依赖格式的试卷，存在小幅准确度风险（可接受）；对多格式、尤其是 PDF 等，准确度是提升（从 0 到有）。**

### 3. 鲁棒性

- **单一入口、单一中间表示**：所有格式都变成「Markdown 文本 + 可选图片列表」，解析器只需面对一种输入形态，边界情况（空段、奇怪标题）集中在一处处理，**鲁棒性更好**。
- **格式差异被前移**：各源格式的差异由 MarkItDown 消化，我们只需应对「Markdown 的多样性」（如 `#` 数量、列表符号等），**比同时应对 docx 与 PDF 两套世界更稳**。
- **依赖与维护**：MarkItDown 由 Microsoft 维护、生态成熟；转换问题可随上游修复而改善，我们只需跟进版本与 API，**长期鲁棒性更好**。

结论：**统一走 MarkItDown 后，鲁棒性会更好。**

---

## 与当前「结构优先 + 格式辅助」的关系

- 我们在 [STRUCTURE_FORMAT_ANALYSIS.md](./STRUCTURE_FORMAT_ANALYSIS.md) 里已说明：**非 Word（MarkItDown 输出）没有字号/缩进，但可以用 Markdown 的 `#`/`##` 当「格式」**，`#`/`##` 视为大题边界，其下到下一标题之间做小题拆分。
- 若采用「统一先转 Markdown」：
  - **Word 路径**：不再从 docx 抽 `ParagraphFormatInfo`，改为用 Markdown 的标题层级（`#`/`##`）做「格式辅助」；与现有「文字 + 宽松大题」规则并存。
  - **其他格式**：同样用「标题层级 + 文字规则」，行为与当前设计一致，只是输入统一为 Markdown。

即：**统一 Markdown 后，仍然可以坚持「结构优先」；只是「格式」从 docx 的 pt/样式变为 Markdown 的标题层级，复杂度与准确度、鲁棒性的权衡如上。**

---

## 实现要点（若采纳）

1. **入口**  
   - 按扩展名或 MIME 判断是否支持（MarkItDown 支持则支持）；  
   - 所有支持格式：`MarkItDown().convert(file_path)`（或等价的 stream API）得到 `result.text_content`（及若有的图片信息）。

2. **Markdown → 解析器输入**  
   - 将 `result.text_content` 拆成「段落列表」：可先按 `\n\n` 拆段，再对每段识别是否以 `#`/`##`/`###` 开头，得到「段落 + 标题层级」的序列；  
   - 若保留与现有 `_parse_structure(paragraphs, paragraph_formats)` 的兼容，可把「标题层级」映射为简化的「格式」：例如 `heading_level in (1,2,3)` 的段落视为「像大题」的格式，传入现有逻辑；或单独写 `_parse_structure_from_markdown(md_text)`，内部先拆段与标题再调用同一套结构/小题/答案逻辑。

3. **图片**  
   - 查清 MarkItDown 的 API：转换结果是否包含图片路径或 base64；若 Markdown 里是 `![](path)`，需从结果中收集 path 与占位符的对应关系，再在题目 content 里保留占位符，与现有 `image_paths` 约定一致。

4. **兼容与回退**  
   - 若短期内希望保留「纯 Word 高精度」：可保留「仅当输入为 .docx 且配置开启时走 python-docx 直解」的分支，其余统一 MarkItDown；长期可考虑全部收口到 MarkItDown。

---

## 建议结论（结合当前场景）

- **若场景以 Word 为主、且大量试卷依赖「无明确一. 二.、靠字号/加粗区分大题」**：  
  可保留当前 Word 直解 + 格式辅助，**同时**增加「非 Word 或可选路径：MarkItDown → Markdown → 同一套结构解析」，逐步验证 Markdown 路径对 Word 的转换质量；若验证满意，再统一为「全部先转 Markdown」。

- **若希望尽快支持 PDF/多格式、并愿意接受 Word 上「格式」从 pt/样式降为标题层级**：  
  **建议采用「统一先转 Markdown 再解析」**：复杂度与维护成本更低，准确度在多格式下更好，鲁棒性更好；对 Word 的少量准确度损失可通过「标题层级 + 文字规则」和后续 MarkItDown 对 Word 的改进来弥补。

- **若当前仅服务 Word、且暂无 PDF 等需求**：  
  可以暂不统一，仅把「其他格式通过 MarkItDown 转换」真正落地为可选分支，与现有 Word 直解并存，待有多格式需求时再全面切到「统一 Markdown」。

---

## 小结

| 维度     | 统一「先 MarkItDown → Markdown 再解析」 |
|----------|----------------------------------------|
| **复杂度** | 降低（单链路、少格式分支）             |
| **准确度** | Word 略可能降（格式变标题层级），多格式从无到有则提升 |
| **鲁棒性** | 提升（单一中间表示、依赖成熟库）       |

在**当前场景**下：若有多格式或扩展需求，**值得统一到 MarkItDown；若短期仅 Word 且非常依赖现有格式辅助，可先双轨（Word 直解 + 可选 MarkItDown 路径），再视效果决定是否全面统一。**
