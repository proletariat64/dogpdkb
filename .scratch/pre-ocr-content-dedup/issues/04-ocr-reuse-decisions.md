# 04: 验证 OCR 来源并给出复用动作

Type: task
Blocked by: 03
Status: ready-for-agent

**What to build:** 调用者只有在 OCR 成功、产物可用且来源和配置兼容时才能得到复用建议。组合文本、资源和元数据状态，完整实现六种动作的保守决策。

- [ ] 验证原始资源、prepared image、OCR block 的来源对应关系；prepared image 必须是经过验证的本地 JPEG，不因扩展名或成功标记就视为有效。
- [ ] 分别验证成功记录、产物实际存在及可用、OCR 所用 prepared image、预处理及 OCR 配置兼容性；证据不足不宣称可复用。
- [ ] SKIP_VERIFIED 要求文本相等或满足所选规范化契约、所有资源确认未变、所需 OCR 成功可用兼容且无元数据更新；无图片文档不要求不存在的 OCR 产物。
- [ ] 完整支持 UPDATE_METADATA_ONLY、UPDATE_TEXT_REUSE_OCR、PROCESS_CHANGED_RESOURCES、PROCESS_REQUIRED、UNKNOWN，输出理由和各维度状态；区分确定缺少／不兼容产物与来源依赖不可验证。
- [ ] 明确动作优先级与混合状态行为；资源或来源未知时不得用已确认的局部变化掩盖未知，仍保留已确认变化和所需资源列表。
- [ ] CLI 黑盒覆盖同文同资源跳过、改名／移动、正文变化图片未变、仅图片变化、无图片、新文档，以及 prepared image／预处理／OCR 配置不兼容、成功记录存在但产物丢失。
- [ ] 本票所需正文变化可先使用字节不同的既有状态，不依赖 Myers 展示完成；不承诺现有 mdocr 已支持按图片复用。
