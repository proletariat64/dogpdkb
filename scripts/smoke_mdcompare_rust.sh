#!/usr/bin/env bash
set -euo pipefail

binary=$1
headers=$(LC_ALL=C readelf --wide --program-headers --dynamic "$binary")
if [[ $headers == *" INTERP "* || $headers == *"(NEEDED)"* ]]; then
    printf '%s\n' 'mdcompare smoke failed: release binary has dynamic dependencies' >&2
    exit 1
fi

workspace=$(mktemp -d)
trap 'rm -rf -- "$workspace"' EXIT
cp -- "$binary" "$workspace/mdcompare"
cd -- "$workspace"

mdcompare() {
    env -i PATH=/nonexistent ./mdcompare "$@"
}

expect() {
    local output=$1 expected=$2
    if [[ $output != *"$expected"* ]]; then
        printf 'mdcompare smoke expected %s in:\n%s\n' "$expected" "$output" >&2
        exit 1
    fi
}

expect "$(mdcompare --version)" 'mdcompare '
printf '%s\n' '# Report' '' 'The original body.' > before.md
printf '%s\n' '# Report' '' 'The updated body.' > after.md
draft=$(mdcompare draft before.md after.md --plan work.json)
expect "$draft" '"command":"draft","state":"complete"'
result=$(mdcompare run --plan work.json)
expect "$result" '"command":"run","state":"complete"'
status=$(mdcompare status --plan work.json)
expect "$status" '"command":"status","state":"complete"'
result_json=$(<work.mdcompare/result.json)
expect "$result_json" '"mdcompare-semantic-result-1"'
expect "$result_json" '"AUTHORED_BODY_CHANGED"'
test -s work.mdcompare/commit.json
test -s work.mdcompare/diff-index.json

printf '%s\n' 'Rust smoke passed: static mdcompare completed an offline comparison from an isolated directory.'
