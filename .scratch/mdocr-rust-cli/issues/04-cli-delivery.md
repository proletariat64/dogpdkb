# Wire the command interface and verify the standalone release

Status: resolved
Type: task
Blocked by: 01, 02, 03

Integrate the public commands, explicit purge lifecycle, embedded English help,
and process output contracts. Verify the installed-style interface, static ELF,
loopback provider execution and the acceptance cases in [the specification](../spec.md).

## Answer

The standalone command interface is integrated in `7883c46` and the review
fixes are in `3a34b57`. All 32 Rust tests, formatting, and Clippy passed on the
integration branch. Verified both OCR modes through local provider fixtures.
Verified the optimized Linux x86-64 ELF has no dynamic interpreter or needed
shared libraries and ran a relocated copy with no helper programs on PATH.
The offline diagnostic, English manual, final JSON and progress
streams, work plan execution, and explicit purge passed their local checks.
Production provider and deployment acceptance are separate from this ticket.
