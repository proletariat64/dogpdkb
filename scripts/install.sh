#!/usr/bin/env bash
# Rendered for each Release by publish_release.py; never requires AntCode login.
set -euo pipefail

VERSION='@VERSION@'
RELEASE_BASE_URL='@RELEASE_BASE_URL@'
WHEEL='@WHEEL@'
WHEEL_SHA256='@WHEEL_SHA256@'

fail() { printf 'dogpdkb install: %s\n' "$*" >&2; exit 1; }
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Use install.sh from a published Release, not the source template.'
[[ "$WHEEL_SHA256" =~ ^[a-f0-9]{64}$ ]] || fail 'Invalid release checksum.'
[[ "$RELEASE_BASE_URL" == https://* ]] || fail 'Release downloads require HTTPS.'
[[ "$(uname -s)" == Linux ]] || fail 'This installer supports Linux.'
for tool in curl sha256sum mktemp readlink flock; do
    command -v "$tool" >/dev/null || fail "Missing prerequisite: $tool"
done

PREFIX="${DOGPDKB_HOME:-$HOME/.local/share/dogpdkb}"
BIN_DIR="${DOGPDKB_BIN_DIR:-$HOME/.local/bin}"
CONFIG_DIR="${DOGPDKB_CONFIG_DIR:-$HOME/.config/dogpdkb}"
# Company network default. Override for another trusted mirror or public PyPI.
INDEX="${DOGPDKB_PYPI_INDEX:-https://pypi.antfin-inc.com/simple/}"
for path in "$PREFIX" "$BIN_DIR" "$CONFIG_DIR"; do
    [[ "$path" == /* && "$path" != / && "$path" != *$'\n'* ]] || fail 'Installation paths must be absolute, single-line, and not /.'
done
mkdir -p "$PREFIX" "$BIN_DIR"
PREFIX=$(cd "$PREFIX" && pwd -P)
BIN_DIR=$(cd "$BIN_DIR" && pwd -P)
CONFIG_DIR=$(readlink -m -- "$CONFIG_DIR")
[[ ! -L "$PREFIX/versions" ]] || fail 'Refusing a symlinked versions directory.'
for path in "$BIN_DIR" "$CONFIG_DIR"; do
    [[ "$path" != "$PREFIX/versions" && "$path" != "$PREFIX/versions/"* &&
       "$PREFIX/versions" != "$path" && "$PREFIX/versions" != "$path/"* ]] || fail 'Configuration and commands must not overlap disposable environments.'
done
[[ ! -L "$PREFIX/.lifecycle.lock" ]] || fail 'Refusing a symlinked installation lock.'
if [[ -n "${DOGPDKB_LOCK_FD:-}" ]]; then
    [[ "$DOGPDKB_LOCK_FD" =~ ^[0-9]+$ ]] || fail 'Invalid inherited installation lock.'
    [[ "$(readlink "/proc/$$/fd/$DOGPDKB_LOCK_FD")" == "$PREFIX/.lifecycle.lock" ]] || fail 'Invalid inherited installation lock.'
    exec 9>&"$DOGPDKB_LOCK_FD"
else
    exec 9>"$PREFIX/.lifecycle.lock"
fi
flock -n 9 || fail 'Another installation operation is running.'

validate_environment() {
    local directory="$1"
    [[ "${directory%/*}" == "$PREFIX/versions" &&
       "${directory##*/}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[A-Za-z0-9]{6}$ &&
       ! -L "$directory" && "$(readlink -m -- "$directory")" == "$directory" &&
       -f "$directory/pyvenv.cfg" && -f "$directory/bin/dogpdkb" && -f "$directory/bin/mdocr" ]] || fail 'Refusing an unmanaged environment.'
    if [[ -e "$directory/.dogpdkb-managed" || -L "$directory/.dogpdkb-managed" ]]; then
        [[ ! -L "$directory/.dogpdkb-managed" ]] || fail 'Invalid ownership marker.'
        local rows=()
        mapfile -t rows < "$directory/.dogpdkb-managed"
        [[ "${#rows[@]}" == 4 && "${rows[0]}" == 1 && "${rows[1]}" == "$PREFIX" &&
           "${rows[2]}" == "$BIN_DIR" && "${rows[3]}" == "$CONFIG_DIR" ]] || fail 'Invalid ownership marker or changed installation paths.'
    else
        [[ "$PREFIX" == "$HOME/.local/share/dogpdkb" && "$BIN_DIR" == "$HOME/.local/bin" &&
           "$CONFIG_DIR" == "$HOME/.config/dogpdkb" &&
           -L "$BIN_DIR/dogpdkb" && "$(readlink "$BIN_DIR/dogpdkb")" == "$PREFIX/current/bin/dogpdkb" ]] || fail 'Unmarked custom installations cannot be replaced safely.'
    fi
}
OLD_ENV=''
check_links() {
    if [[ -e "$PREFIX/current" || -L "$PREFIX/current" ]]; then
        [[ -L "$PREFIX/current" ]] || fail 'Refusing to replace an unmanaged current path.'
        local selected
        selected=$(readlink "$PREFIX/current")
        [[ "$selected" == "$PREFIX/versions/"* ]] || fail 'Refusing to replace an unmanaged current link.'
        validate_environment "$selected"
        OLD_ENV="$selected"
    fi
    for name in dogpdkb mdocr; do
        if [[ -e "$BIN_DIR/$name" || -L "$BIN_DIR/$name" ]]; then
            [[ -L "$BIN_DIR/$name" && "$(readlink "$BIN_DIR/$name")" == "$PREFIX/current/bin/$name" ]] || fail "Refusing to overwrite $BIN_DIR/$name"
        fi
    done
}
check_links

TMP=$(mktemp -d)
ENV_DIR=''
ACTIVATED=0
CURRENT_LINK=''
cleanup() {
    rm -rf -- "$TMP"
    # A signal can arrive after the rename but before ACTIVATED is assigned.
    if [[ "$ACTIVATED" == 0 && -n "$ENV_DIR" &&
          "$(readlink "$PREFIX/current" 2>/dev/null || true)" != "$ENV_DIR" ]]; then
        rm -rf -- "$ENV_DIR"
    fi
    if [[ -n "$CURRENT_LINK" ]]; then rm -f -- "$CURRENT_LINK"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
fetch() {
    curl --disable --fail --silent --show-error --location --retry 2 \
        --connect-timeout 10 --max-time 180 --proto '=https' --proto-redir '=https' \
        "$1" --output "$2"
}
fetch "$RELEASE_BASE_URL/$WHEEL" "$TMP/$WHEEL"
printf '%s  %s\n' "$WHEEL_SHA256" "$TMP/$WHEEL" | sha256sum -c -

if command -v uv >/dev/null; then
    UV=$(command -v uv)
elif [[ -x "$PREFIX/tools/uv" ]]; then
    UV="$PREFIX/tools/uv"
else
    fetch 'https://astral.sh/uv/0.11.7/install.sh' "$TMP/uv-install.sh"
    UV_UNMANAGED_INSTALL="$PREFIX/tools" sh "$TMP/uv-install.sh"
    UV="$PREFIX/tools/uv"
fi
[[ -x "$UV" ]] || fail 'uv bootstrap did not produce an executable.'
export UV_NO_PROGRESS=1
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-30}"
export UV_HTTP_RETRIES="${UV_HTTP_RETRIES:-2}"
mkdir -p "$PREFIX/versions"
ENV_DIR=$(mktemp -d "$PREFIX/versions/$VERSION.XXXXXX")
"$UV" venv --no-config --no-project --python "${DOGPDKB_PYTHON:-3.12}" "$ENV_DIR"
"$UV" pip install --no-config --python "$ENV_DIR/bin/python" --default-index "$INDEX" "$TMP/$WHEEL"
[[ "$("$ENV_DIR/bin/dogpdkb" --version)" == "dogpdkb $VERSION" ]] || fail 'Installed version does not match the Release.'
"$ENV_DIR/bin/dogpdkb" doctor --package
# This marker records paths, not authority to remove arbitrary directories.
printf '1\n%s\n%s\n%s\n' "$PREFIX" "$BIN_DIR" "$CONFIG_DIR" > "$ENV_DIR/.dogpdkb-managed"

# Preserve existing or partially configured instances, including symlinks.
if [[ ! -e "$CONFIG_DIR/dogpdkb.toml" && ! -L "$CONFIG_DIR/dogpdkb.toml" && \
      ! -e "$CONFIG_DIR/books.json" && ! -L "$CONFIG_DIR/books.json" ]]; then
    "$ENV_DIR/bin/dogpdkb" init-config -o "$CONFIG_DIR"
fi
check_links
for name in dogpdkb mdocr; do
    ln -s "$PREFIX/current/bin/$name" "$TMP/$name"
    mv -Tf -- "$TMP/$name" "$BIN_DIR/$name"
done
ln -s "$ENV_DIR" "$PREFIX/.current.$$"
CURRENT_LINK="$PREFIX/.current.$$"
mv -Tf -- "$CURRENT_LINK" "$PREFIX/current"
CURRENT_LINK=''
ACTIVATED=1
# Remove only the previously selected, validated environment. Never sweep versions.
if [[ -n "$OLD_ENV" && "$OLD_ENV" != "$ENV_DIR" ]]; then
    validate_environment "$OLD_ENV"
    rm -rf -- "$OLD_ENV"
fi
printf 'Installed dogpdkb %s\nCommands: %s/dogpdkb and %s/mdocr\nConfiguration: %s\n' "$VERSION" "$BIN_DIR" "$BIN_DIR" "$CONFIG_DIR"
printf 'Edit the disabled examples before syncing; Graphify and gateway setup remain separate.\n'
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) printf 'Add commands to this shell: export PATH=%q:"$PATH"\n' "$BIN_DIR" ;;
esac
printf 'Preflight: %q doctor --config %q\n' "$BIN_DIR/dogpdkb" "$CONFIG_DIR/dogpdkb.toml"
