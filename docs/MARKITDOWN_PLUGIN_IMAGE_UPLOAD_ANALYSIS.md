# MarkItDown 插件与「图片上传后替换为 URL」方案分析

## 一、MarkItDown 是否支持插件？

**结论：支持。**

- 通过 **entry point** 注册：`[project.entry-points."markitdown.plugin"]`，例如 `your_plugin = your_package`。
- 插件需提供：
  - `__plugin_interface_version__ = 1`
  - `register_converters(markitdown: MarkItDown, **kwargs)`，在内部调用 `markitdown.register_converter(YourConverter(), priority=...)`。
- 自定义 **DocumentConverter** 子类，实现 `accepts(stream_info)` 与 `convert(file_stream, stream_info, **kwargs)`，返回 `DocumentConverterResult`。
- 启用方式：
  - Python：`MarkItDown(enable_plugins=True)`
  - CLI：`markitdown --use-plugins your_file.docx`
- 官方示例：`pip install markitdown-sample-plugin`，参考 [Discussion #1099](https://github.com/microsoft/markitdown/discussions/1099)。

因此，**可以在 MarkItDown 基础上写插件**，在转换阶段介入（例如自定义 DOCX 转换器，控制图片如何写出）。

---

## 二、是否有现成的「转出图片 → 上传到服务 → 用 URL 替换」的插件？

**结论：没有可直接复用的。**

已查情况：

| 类型 | 说明 |
|------|------|
| **官方 sample plugin** | 仅演示插件结构，不处理图片上传。 |
| **Discussion #1099 示例** | 有人实现 `DocxConverterWithImages`：继承 `DocxConverter`，用 mammoth 的 `ImageWriter(output_dir)` 把图片**写到本地目录**，markdown 里是**本地文件路径**，不是「上传到 HTTP 服务得到 URL」。 |
| **markdown-imgur-upload** | 针对**已有 markdown 中的本地图片文件**，上传到 **Imgur** 并替换链接；不是 MarkItDown 插件，且目标为 Imgur，不是我们的 asset-service。 |
| **markdown-it-img-replacer** | JS 库，对 markdown 里已有 URL 做替换，与「转换时上传」无关。 |

未发现任何现成插件满足：

- 在 **MarkItDown 转换流程内** 或 **专为 MarkItDown 设计**；
- 将图片上传到**自定义 HTTP 服务**（如 asset-service）；
- 在生成的 markdown 里使用**上传后得到的 URL**。

因此，若要「在 MarkItDown 里直接得到带图片 URL 的 markdown」，需要**自己开发**（见下）。

---

## 三、若自己开发插件，可行思路

### 1. 插件内实现「上传并替换为 URL」

- **做法**：写一个 MarkItDown 插件，提供自定义 **DocxConverter**（继承自官方 `DocxConverter`）。
  - 在转换 DOCX 时，不用默认的 base64 / 默认 ImageWriter，而是为 mammoth 提供自定义的 `convert_image`：
    - 每张图：拿到 blob（或流）→ 调用 **asset-service 上传** → 得到 URL。
    - 返回给 mammoth 的 `img` 的 `src` 设为该 URL。
  - 这样 MarkItDown 一次转换输出的 markdown 里，图片已经是 **https://...**，无需再跑后处理。
- **依据**：mammoth 支持 `convert_image=mammoth.images.img_element(your_handler)`，handler 可自定义；Discussion #1099 的示例已证明可通过子类 `DocxConverter` 注入 `convert_image`，仅需把「写本地文件」改成「上传并返回 URL」。
- **注意**：需处理同步/异步（若 asset-service 为 async）、错误重试、以及插件包与 entry point 的发布与安装；且仅影响 DOCX，若后续要支持 PPTX 等，需再对接对应 converter 的图片出口。

### 2. 维持当前「后处理」方案（推荐，无需插件）

- **当前流程**：MarkItDown 转出 markdown（`keep_data_uris=True`）→ **后处理**：从 markdown 中提取 data URL → 调用 asset-service 上传 → 在 markdown 中把 data URL 替换为返回的 URL。
- **实现位置**：`question-hub-document-service` 的 `MarkdownConverter` + `ImageProcessor` + `document_consumer` 已实现该流程。
- **优点**：
  - 不依赖 MarkItDown 插件机制，不关心其内部版本/接口变化。
  - 与格式解耦：凡 MarkItDown 能转成带 data URL 的 markdown 的格式（DOCX/PPTX 等），同一套后处理都适用。
  - 逻辑清晰：先「转成 markdown」，再「统一处理图片」。
- **与插件方案对比**：效果等价（最终 markdown 都是「图片为 URL」）；插件方案把「上传」提前到转换内部，少一步后处理，但需单独维护插件和与 asset-service 的集成。

---

## 四、建议

1. **不引入现成插件**：目前没有「MarkItDown + 上传到自定义服务并替换为 URL」的现成插件可直接使用。
2. **优先继续用现有后处理**：保持「MarkItDown → 带 base64 的 markdown → ImageProcessor 上传 asset-service → 替换为 URL」的流程即可，无需为「在 MarkItDown 内部完成上传」而开发插件，除非有强需求（例如必须单次调用、无后处理步骤）。
3. **若将来要做插件**：再实现一个 MarkItDown 插件，自定义 DocxConverter + mammoth `convert_image`，在回调里请求 asset-service 上传并返回 URL；可参考 Discussion #1099 的 `DocxConverterWithImages` 结构，把 `ImageWriter` 换成「UploadToAssetServiceWriter」。

---

## 五、参考链接

- [MarkItDown Discussion #1099 - plugin priority & DocxConverterWithImages 示例](https://github.com/microsoft/markitdown/discussions/1099)
- [MarkItDown Issue #317 - Images in Docx / 暴露 convert_image 等选项](https://github.com/microsoft/markitdown/issues/317)
- [PR #1140 - keep_data_uris](https://github.com/microsoft/markitdown/pull/1140)
- [mammoth convert_image / ImageWriter](https://pypi.org/project/mammoth/)（DOCX 转 HTML 时图片处理）
