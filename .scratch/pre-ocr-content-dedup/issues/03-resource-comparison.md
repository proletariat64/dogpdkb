# 03: 资源独立比较与变化资源清单

Type: task
Blocked by: 01
Status: resolved

**What to build:** 调用者可独立确认资源相等、变化或未知，并取得需要处理的资源清单；Markdown 相同或 URL 相同均不足以证明资源未变。

- [x] 比较调用者提供的本地资源字节或具有明确可信契约的不可变版本证据，不执行下载；仅 URL、缺失字节或不可验证证据返回资源未知。
- [x] 同 URL 不同字节必须识别为变化，不同 URL 相同字节可识别为资源相等，但不抹掉链接本身的文本／元数据变化。
- [x] 结构分析能够关联清单资源与 Markdown 使用位置；图片及其他外部资源均纳入验证。无法完整识别或关联的依赖保持未知，不因清单遗漏而误跳过。
- [x] 资源顺序、重复次数、标题、替代文本及链接含义保留；不因字节相同而折叠出现位置或覆盖正文差异。
- [x] 明确新增／变化的资源输出 PROCESS_CHANGED_RESOURCES 与所需资源列表；混合未知时保留已确认局部变化，未知不得被掩盖为整篇可安全处理或跳过。
- [x] 资源相等与 OCR 可复用分别输出；本票不具备完整 OCR 证明时保持 OCR 未知或需处理，不能仅凭原图相等跳过。
- [x] CLI 黑盒覆盖同 URL 换图、不同 URL 同字节、长截图仅一位数字变化、缺资源、资源顺序／次数／替代文本变化，以及其他外部资源。

## Answer

Implemented independent resource byte comparison, explicit version evidence, dependency discovery and stable per-resource processing lists. Source contract and the OCR handoff are documented in the resource evidence guide; original bytes use the explicit `path` field and every read goes through InputReader. IDs pair logical resources across URL changes. Same URL is never equality evidence; changed/unknown combinations retain known work while the document remains UNKNOWN.

TDD at the approved CLI seam observed red before green for replaced image bytes, reference/HTML dependency discovery, trusted S3 versions, mixed HTML/Markdown occurrence ordering, autolink downloads, indented list images, partial parenthesized targets and angle-bracket occurrence counting. The 24 resource cases also cover different URLs with equal bytes, real generated long screenshot digit changes, duplicate/order/alt changes, missing reads, untrusted/null/mismatched versions, ordinary navigation/code examples and budget excess.

Verification after merging integration da6c451: all four mdcompare test modules passed, 97 tests; Ruff check and format passed; Pyright for src/mdcompare reported zero errors/warnings with the explicit cached typeshed; doc-guard passed 34 documents; git diff --check passed. The graph was refreshed and impact analysis identified comparison/CLI orchestration; subprocess CLI coverage is proven by the tests rather than inferred graph edges.

Resource equality does not authorize OCR reuse. The initial OCR boundary still conservatively returns UNKNOWN for images; ticket04 independently verifies prepared images, provenance/configuration and artifacts before complete actions become available. No network requests, persistent hash index, production mutations or corpus operations were introduced. Immutable proof is limited to the explicit caller-captured non-null s3-version-id-v1 contract, documented with AWS primary-source references.


## Comments

Post-merge corrective verification found a false resource-free decision from a regex matching only part of a backtick closing run. Replaced regex code masking with exact maximal-run scanning, respecting opener escape parity and preserving uncertain multiline/HTML/container contexts. Corrected fenced-code info/closer rules and even-backslash image markers. Added CLI regressions for mismatched runs, escaped openers, closing backslashes inside proven spans, invalid fence closers/openers, indented closers, valid unclosed fences, paragraph boundaries, HTML-looking contexts, and odd/even image escapes. CommonMark 0.31.2 primary rules are linked in the resource guide.

Corrective red → green: three explicit mismatch/even-escape cases first reproduced unsafe skip; a subsequent indented valid closer test caught an overly narrow close rule before commit. Final resource tests: 43 passed; all four current mdcompare modules: 116 passed. Pyright: zero errors/warnings. Original inputs remain read-only; no new dependencies or production operations.
