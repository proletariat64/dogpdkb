# Markdown Image OCR

This context prepares images referenced by Markdown documents and replaces eligible images with traceable OCR Markdown.

## Language

**Prepared image**:
A validated local JPEG representation of a Markdown image, retained as the traceable source for OCR and later re-OCR.
_Avoid_: Normalized image, asset

**OCR block**:
A marked Markdown region containing OCR-produced Markdown and a link to its prepared image.
_Avoid_: OCR result, replacement block
