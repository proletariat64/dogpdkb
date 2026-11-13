# feat: Install Skylark latest with the managed release installer

Type: task
Status: resolved

## Scope

Use npm install pi-skylark@latest in the candidate managed environment. No pinned
version, extra release attachment, source build or separate repository push.
Require existing node/npm, expose skylark beside the Python commands and remove
only its owned link during uninstall. Reinstall resolves latest again. Failed
npm/startup must preserve the current installation.

## Acceptance

Focused installer/lifecycle/help tests, real npm latest startup and isolated
managed installation UAT. Then publish the approved merged source via the existing
release flow. Keep credentials, SSO, corpus and other npm installations unchanged.

## Answer

The existing release installer now requires node/npm, installs pi-skylark@latest
inside its candidate environment, checks startup before activation, and manages
the skylark command link alongside dogpdkb/mdocr/mdcompare. No added artifact or
pinned version. Existing global npm installations and gateway/SSO are separate.

94 focused installer/lifecycle/help tests pass. Real npm latest resolves to
pi-skylark 0.1.1. Isolated UAT with real wheel, real uv and real npm passed fresh
install, all four PATH commands, same-version reinstall, npm failure preserving
the active environment, owned uninstall and configuration preservation.
Evidence: /tmp/dogpdkb-skylark-uat-k_0k0ulv/result.json. Wheel downloads used an
offline transport fixture; no authenticated source download or model call ran.

Prepare v0.2.1 for formal release; v0.2.0 is already published on AntCode.
