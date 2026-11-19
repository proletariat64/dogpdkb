# Pi-backed OCR runtime prerequisites

Paddle does not install or load Node. For LLM mode, install Pi coding agent (tested model-runtime preflight: 1.5.2) and Node meeting that Pi release's engine requirement (22.19.0 or newer for Pi 1.5.2). The bridge locates the installed SDK from the real path of `pi` or an explicit Pi package root via `MDOCR_PI_SDK_DIR`. OpenAI and OpenAI Codex Responses routes only; log in from Pi and use the exact provider prefix. Pi owns its OAuth store and refresh. `PI_CODING_AGENT_DIR` applies to both credential and model configuration paths. API keys and alternate endpoints are not eligible.

Install the local Mermaid parser once with `npm install --prefix "$HOME/.local/share/mdocr/node" --ignore-scripts mermaid@12.3.0 jsdom@26.1.0`. Set `MDOCR_NODE_DEPENDENCIES` to that prefix. The parser loads locally, can run offline, and receives no OAuth credential. The bridge refuses malformed diagrams or explicit Mermaid output when the parser is unavailable; it does not pretend a regex validates Mermaid syntax.

The bridge exposes no live vision input token ceiling from the installed provider descriptors. A hard total-token limit must fail closed unless the runner has a justified upper bound. Request-count limits are independent and unaffected. Do not interpret a successful local runtime preflight as real inference acceptance for either provider.
