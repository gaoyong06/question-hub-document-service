# 统一以 markdown_content 为源的转换与解析设计

## 一、你提出的方案（结论：合理）

1. **Word（.doc / .docx）** → 使用 python-docx 得到 markdown → 写入 `markdown_content`
2. **其他格式** → 使用 MarkItDown 得到 markdown → 写入 `markdown_content`
3. **试卷结构化** → 统一依赖 `markdown_content`，即试卷结构**只**从 markdown 解析得到

该思路**合理**，且能达成：
- 单一中间表示：任何格式都先变成 `markdown_content`
- 单一解析路径：试卷结构只来自 `markdown_parser.parse_markdown_to_exam(markdown_content)`，无两套逻辑
- Word 图文同行：由 python-docx 按段落+run 生成 markdown，自然保留「A. 图 B. 图」同一行

---

## 二、需要明确的细节与优化建议

### 2.1 .doc 与 .docx 的区分（已明确）

- **python-docx 只支持 .docx**（OOXML），不支持老版 .doc（Binary）。
- **已采用**：
  - **.docx**：走 python-docx（`docx_to_markdown`）→ markdown_content
  - **.doc**：先用 LibreOffice `soffice` 转为 .docx，再走 python-docx；若转换不可用或失败则回退 MarkItDown

### 2.2 图片：统一「先到 markdown，再上传」

- **Word（.docx）**：python-docx 提取段落时已得到 `image_paths`（本地临时文件），生成 markdown 时用 `{{IMAGE_N}}` 或 `![](local_path)`；**需要对这份 markdown 做一次图片上传**，把占位/本地路径换成 URL，得到的才是最终写入的 `markdown_content`。
- **其他格式**：MarkItDown 已产出含 data URL 或路径的 markdown，现有 `process_images_in_markdown` 上传并替换为 URL。
- 建议：**所有格式的 `markdown_content` 存库/下游使用前，都是「已上传、图片为 URL」的版本**，这样行为一致，下游无需再区分来源。

### 2.3 元数据（metadata）统一（可选但推荐）

- MarkItDown 会返回 `metadata`（如 title）；consumer 会用 `metadata.get("title")` 等补全 document_title。
- docx 路径若也产出 `(markdown_content, metadata)`，便于 consumer 同一套逻辑（例如 doc_title 从 metadata 取）。metadata 可从 `doc.core_properties.title`、首段等组装。

### 2.4 生成 markdown 的约定（便于 markdown_parser 正确解析）

- 从 python-docx 生成 markdown 时，建议：
  - 段落之间用 `\n\n` 连接（与现有按 `\n\s*\n` 拆段一致）；
  - 同一段内保持「A. ![]() B. ![]()」在同一行（不插入多余换行），这样一道题的题干+选项不会被打散成多段。
- 这样「试卷结构统一依赖 markdown_content」时，markdown_parser 的按段、按题号/选项切题才能与 Word 版式一致。

---

## 三、实现流程小结（无额外信息时的推荐）

| 步骤 | .docx | 其他格式 |
|------|--------|----------|
| 1. 得到「原始」markdown | DocumentParser：段落+图片 → 拼成字符串（`{{IMAGE_N}}` 或 `![](local_path)`） | MarkItDown.convert → markdown |
| 2. 图片上传并替换 | 对上述 markdown 调 `process_images_in_markdown`（或先上传 image_paths，再在 markdown 中替换占位符为 `![](url)`） | 现有 `process_images_in_markdown` |
| 3. 得到 markdown_content | 步骤 2 的输出 | 步骤 2 的输出 |
| 4. 试卷结构 | `markdown_parser.parse_markdown_to_exam(markdown_content)` | 同上 |

**.doc**：已实现为先转 .docx（soffice）再走 python-docx，失败则回退 MarkItDown。

---

## 四、已确认的决策（已落实）

1. **.doc**：支持；先转 .docx 再走 python-docx，转换失败则用 MarkItDown。
2. **parse_document**：已删除；无调用方依赖，结构统一从 markdown 解析。
3. **docx 图片上传**：方案 A，统一走 `process_images_in_markdown`。

---

## 五、结论

- 你的三点思路（Word→python-docx→markdown_content，其他→MarkItDown→markdown_content，试卷统一从 markdown_content 解析）**合理**，可以按此实现。
- 建议在设计中**明确 .docx 用 python-docx、.doc 用 MarkItDown**，并约定**所有格式的 markdown_content 均为「图片已上传为 URL」的版本**；docx 路径可顺带产出 metadata 与 MarkItDown 对齐，便于 consumer 统一处理。
- 若你确认 .doc 策略、parse_document 的依赖、以及图片上传的入口偏好，可以再细化到具体接口与调用顺序（例如在 `document_consumer` / `markdown_converter` 中的分支与入参）。

---

## 六、已落实的结论与实现（当前代码）

以下三点已确认并落地：

1. **.doc 支持**：先尝试用系统 LibreOffice（`soffice --headless --convert-to docx`）将 .doc 转为 .docx，再走 python-docx；若未安装 soffice 或转换失败，.doc 回退为 MarkItDown。
2. **parse_document 已删除**：无调用方依赖其题目列表；结构统一由 `markdown_parser.parse_markdown_to_exam(markdown_content)` 得到。`DocumentParser` 仅保留 `download_file`、`cleanup`、`convert_doc_to_docx`、`docx_to_markdown` 等与「生成 markdown」相关的能力。
3. **docx 图片上传：方案 A**：python-docx 生成含 `![](本地绝对路径)` 的 markdown 后，统一走 `process_images_in_markdown` 上传并替换为 URL，与 PDF 等格式一致。

**当前实现要点**：

- **document_consumer._process_document**：按扩展名分支；`.doc` / `.docx` 先尝试 .doc→.docx 转换（仅 .doc），再对 .docx 调 `parser.docx_to_markdown`，否则调 `markdown_converter.convert_to_markdown`；然后统一 `process_images_in_markdown` → `markdown_parser.parse_markdown_to_exam`；临时文件（含转换得到的 .docx）在 finally 中统一 cleanup。
- **DocumentParser**：`convert_doc_to_docx(doc_path)`、`docx_to_markdown(file_path)` 返回 `(markdown, metadata)`；已移除 `parse_document`。
- **容器运行**：服务运行在容器中时，LibreOffice 需**安装在镜像内**（见本仓库 `Dockerfile` 中的 `libreoffice-writer`），不可只装在宿主机；未安装或转换失败时 .doc 会回退 MarkItDown。
