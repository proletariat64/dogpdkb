# Markdown Image OCR

This context prepares images referenced by Markdown documents and replaces eligible images with traceable OCR Markdown.

## Language

**OCR CI**:
Code inspection performed by OpenCodeReview after a pull request. This is separate from Markdown image OCR.
_Avoid_: mdocr CI, OCR document conversion

**mdocr**:
The tool that converts prepared images referenced by Markdown into traceable OCR blocks. It is not the code-review tool.
_Avoid_: OpenCodeReview, OCR CI

**Prepared image**:
A validated local JPEG representation of a Markdown image, retained as the traceable source for OCR and later re-OCR.
_Avoid_: Normalized image, asset

**OCR block**:
A marked Markdown region containing OCR-produced Markdown and a link to its prepared image.
_Avoid_: OCR result, replacement block
