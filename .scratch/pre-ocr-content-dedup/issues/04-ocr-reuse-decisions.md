# 04: 验证 OCR 来源并给出复用动作

Type: task
Blocked by: 03
Status: resolved

**What to build:** 调用者只有在 OCR 成功、产物可用且来源和配置兼容时才能得到复用建议。组合文本、资源和元数据状态，完整实现六种动作的保守决策。

- [x] 验证原始资源、prepared image、OCR block 的来源对应关系；prepared image 必须是经过验证的本地 JPEG，不因扩展名或成功标记就视为有效。
- [x] 分别验证成功记录、产物实际存在及可用、OCR 所用 prepared image、预处理及 OCR 配置兼容性；证据不足不宣称可复用。
- [x] SKIP_VERIFIED 要求文本相等或满足所选规范化契约、所有资源确认未变、所需 OCR 成功可用兼容且无元数据更新；无图片文档不要求不存在的 OCR 产物。
- [x] 完整支持 UPDATE_METADATA_ONLY、UPDATE_TEXT_REUSE_OCR、PROCESS_CHANGED_RESOURCES、PROCESS_REQUIRED、UNKNOWN，输出理由和各维度状态；区分确定缺少／不兼容产物与来源依赖不可验证。
- [x] 明确动作优先级与混合状态行为；资源或来源未知时不得用已确认的局部变化掩盖未知，仍保留已确认变化和所需资源列表。
- [x] CLI 黑盒覆盖同文同资源跳过、改名／移动、正文变化图片未变、仅图片变化、无图片、新文档，以及 prepared image／预处理／OCR 配置不兼容、成功记录存在但产物丢失。
- [x] 本票所需正文变化可先使用字节不同的既有状态，不依赖 Myers 展示完成；不承诺现有 mdocr 已支持按图片复用。


## Answer

Implemented the explicit prepared-jpeg-ocr-v1 capture contract, per-image original/prepared/artifact mapping validation, decoded JPEG and pixel/file budgets, exact typed preprocessing/OCR configuration compatibility, successful marked OCR block verification, and per-image reusable/processing lists. Existing action selection now receives determinate OCR states for all six actions; its priority is documented in the OCR contract. Historical source reconstruction remains owned by ticket 05.

CLI red→green evidence: a captured successful compatible OCR pair first returned UNKNOWN, then SKIP_VERIFIED; an unmapped image first falsely reported OCR not applicable, then UNKNOWN; a prepared-image validation budget initially returned processing, then UNKNOWN; a cyclic artifact source initially prevented reporting, then produced private per-item UNKNOWN. Tests use no OCR service or production inputs.

Validation: 30 OCR CLI cases pass, including typed true/1 and 1/1.0 configuration distinctions, source/block mapping faults, changed or invalid prepared JPEG, missing/empty/failed artifacts, budget excess, mixed local evidence, no-image/attachment and new-document cases. Focused combined validation and final integration merge are recorded in the commit handoff.
