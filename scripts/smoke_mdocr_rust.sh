#!/usr/bin/env bash
set -euo pipefail

binary=$1
headers=$(LC_ALL=C readelf --wide --program-headers --dynamic "$binary")
if [[ $headers == *" INTERP "* || $headers == *"(NEEDED)"* ]]; then
    printf '%s\n' 'mdocrr smoke failed: release binary has dynamic dependencies' >&2
    exit 1
fi

workspace=$(mktemp -d)
trap 'rm -rf -- "$workspace"' EXIT
cp -- "$binary" "$workspace/mdocrr"
cd -- "$workspace"

mdocrr() {
    env -i PATH=/nonexistent ./mdocrr "$@"
}

expect() {
    local output=$1 expected=$2
    if [[ $output != *"$expected"* ]]; then
        printf 'mdocrr smoke expected %s in:\n%s\n' "$expected" "$output" >&2
        exit 1
    fi
}

check() {
    local expected=$1 output
    shift
    output=$(mdocrr "$@")
    expect "$output" "$expected"
}

check 'mdocrr ' --version
check 'mdocrr help all' --help
check 'mdocr-work-1' help all
check '"ok":true' check-mermaid - <<'EOF'
```mermaid
flowchart TD
  A --> B
```
EOF

printf '%s\n' 'smoke-fixture-token' > token.txt
printf '%s\n' 'mode = "paddle"' 'token_file = "token.txt"' 'request_budget = 0' > default.toml
check '"network_checked":false' doctor --config default.toml
printf '%s\n' 'Markdown smoke fixture.' > source.md
check '"status":"drafted"' draft --input-single source.md --output-single output.md --plan work.json
check '"status":"completed"' run --plan work.json
expect "$(<work.mdocr/result.json)" '"mdocr-batch-result-1"'
cmp -- source.md output.md

printf '%s\n' 'Rust smoke passed: static mdocrr ran from an isolated directory with an empty executable search path.'
