#!/usr/bin/env bash
set -euo pipefail

binary=$1
headers=$(LC_ALL=C readelf --wide --program-headers --dynamic "$binary")
if [[ $headers == *" INTERP "* || $headers == *"(NEEDED)"* ]]; then
    printf '%s\n' 'mdcomparer smoke failed: release binary has dynamic dependencies' >&2
    exit 1
fi

workspace=$(mktemp -d)
workspace=$(cd -- "$workspace" && pwd -P)
trap 'rm -rf -- "$workspace"' EXIT
cp -- "$binary" "$workspace/mdcomparer"
cd -- "$workspace"

mdcomparer() {
    env -i PATH=/nonexistent ./mdcomparer "$@"
}

expect() {
    local output=$1 expected=$2
    if [[ $output != *"$expected"* ]]; then
        printf 'mdcomparer smoke expected %s in:\n%s\n' "$expected" "$output" >&2
        exit 1
    fi
}

version=$(mdcomparer --version)
expect "$version" 'mdcomparer '
mkdir before after
printf '%s\n' '# Report' '' 'The original body.' > before/report.md
printf '%s\n' '# Report' '' 'The updated body.' > after/report.md
draft=$(mdcomparer draft before/report.md after/report.md --plan work.json)
expect "$draft" '"command":"draft","state":"complete"'
result=$(mdcomparer run --plan work.json)
expect "$result" '"command":"run","state":"complete"'
status=$(mdcomparer status --plan work.json)
expect "$status" '"command":"status","state":"complete"'
result_json=$(<work.mdcompare/result.json)
expect "$result_json" '"mdcompare-semantic-result-1"'
expect "$result_json" '"AUTHORED_BODY_CHANGED"'
test -s work.mdcompare/commit.json
test -s work.mdcompare/diff-index.json

printf '%s\n' 'Rust smoke passed: static mdcomparer completed an offline comparison from an isolated directory.'
