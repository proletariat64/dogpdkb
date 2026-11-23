"""Graph orchestration contracts through the executable CLI and public artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dogpdkb.graph import _ensure_corpus_whitelist, _git_info_exclude
from scripts.smoke_install import GRAPHIFY_PREFLIGHT, GRAPHIFY_PROOF
from tests.source_fixture import graphify_offline_env, graphify_sse, yuque_binding

ROOT = Path(__file__).resolve().parents[1]
VERSION = {
    "utc": "2026-11-12T10:00:00+00:00",
    "value": "2026-11-12T10:00:00Z",
    "precision": "second",
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path, *, title="Title", corpus_path="corpus"):
    corpus = tmp_path / corpus_path
    corpus.mkdir(parents=True)
    run = tmp_path / "runs/funds/run-graph"
    run.mkdir(parents=True)
    source = corpus / f"{title}-2026-11-12-[42].md"
    source.write_text(
        "# Complete current source\n\nReal full Markdown, never a diff fragment.\n"
    )
    witness = run / "published/0.md"
    witness.parent.mkdir()
    witness.write_bytes(source.read_bytes())
    document = {
        "document_id": "42",
        "version": VERSION,
        "path": str(source),
        "input_copy": str(witness),
        "metadata": {"name": "Title", "source_ref": "https://example.invalid/42"},
        "action": "compile",
        "reason_codes": ["BODY_CHANGED"],
    }
    manifest = run / "manifest.json"
    write(
        manifest,
        {
            "schema": "dogpdkb-compile-1",
            "book": "funds",
            "run_id": run.name,
            "documents": [document],
            "failures": [],
        },
    )
    write(
        run / "run.json",
        {
            "schema": "dogpdkb-run-2",
            "run_id": run.name,
            "targets": {
                "sources": yuque_binding("https://example.invalid/book"),
                "corpus": str(corpus),
                "graph": str(corpus / "graphify-out"),
            },
            "book": {
                "key": "funds",
                "sources": yuque_binding("https://example.invalid/book"),
                "name": "Funds",
            },
            "pid": 999999999,
            "status": "completed",
            "stage": "corpus",
            "started_at": "2026-11-12T10:00:00+08:00",
            "updated_at": "2026-11-12T10:00:00+08:00",
            "counts": {},
            "artifacts": {"manifest": str(manifest)},
            "failures": [],
        },
    )
    write(
        tmp_path / "state/funds/state.json",
        {
            "schema": "dogpdkb-state-2",
            "book": {
                "key": "funds",
                "sources": yuque_binding("https://example.invalid/book"),
                "name": "Funds",
            },
            "documents": {
                "42": {
                    "pending": {"version": VERSION, "metadata": document["metadata"]},
                    "steps": {},
                    "corpus": {
                        "path": str(source),
                        "version": VERSION,
                        "input_copy": str(witness),
                        "metadata": document["metadata"],
                    },
                }
            },
        },
    )
    write(
        tmp_path / "books.json",
        [
            {
                "key": "funds",
                "name": "Funds",
                "sources": [
                    {
                        "key": "team",
                        "type": "yuque",
                        "url": "https://example.invalid/book",
                    }
                ],
                "corpus": corpus_path,
            }
        ],
    )
    tool = tmp_path / "graphify.py"
    tool.write_text(
        GRAPHIFY_PREFLIGHT
        + """import json,os,sys
from pathlib import Path
root=Path(sys.argv[1]); command=sys.argv[2]; args=sys.argv[3:]
def value(flag): return args[args.index(flag)+1]
with (root/'calls.jsonl').open('a') as log: log.write(json.dumps(sys.argv[2:])+'\\n')
assert command=='extract', command
assert '--no-cluster' in args
assert '--no-gitignore' in args
assert value('--backend')=='openai-oauth'
assert value('--max-concurrency')=='3'
assert os.environ['GRAPHIFY_OPENAI_OAUTH_PARALLEL']=='1'
output=Path(os.environ['GRAPHIFY_OUT']); output.mkdir(parents=True,exist_ok=True)
assert Path(args[0])==Path(value('--out'))==root/'corpus'
assert output==root/'corpus/graphify-out'
mode=(root/'mode').read_text() if (root/'mode').exists() else 'success'
if mode=='interrupt': sys.exit(130)
if mode in ('failed','partial'):
 (output/'graph.json').write_text('{"nodes":[{"id":"unpublished"}],"links":[]}')
 sys.exit(1)
if mode=='unknown':sys.exit(1)
if mode=='invalid': (output/'graph.json').write_text('invalid'); sys.exit(0)
if mode=='mutate': next(Path(args[0]).glob('*.md')).write_text('changed during extraction')
if mode=='grow-incomplete':
 (output/'graph.json').write_text(json.dumps({'nodes':[{'id':'old'}, {'id':'new'}], 'links':[]}))
 (output/'cache').mkdir(exist_ok=True)
 (output/'cache/entry').write_text('unpublishable')
 print('[graphify extract] semantic extraction on 1 files via openai-oauth...')
 print('[graphify extract] chunk 1/2 done')
 print('[graphify] WARNING: 1/2 semantic chunk(s) failed — see errors above. Partial results returned.')
else:
 (output/'graph.json').write_text(json.dumps({'nodes':[], 'links':[], 'graph':{}, 'hyperedges':[]}))
 (output/'cache').mkdir(exist_ok=True)
 (output/'cache/entry').write_text('trusted replacement')
"""
        + GRAPHIFY_PROOF
    )
    config = tmp_path / "instance.toml"
    config.write_text(
        '[source]\nbooks_file="books.json"\n[paths]\nstate="state"\nstaging="runs"\n[tools]\ngraphify='
        + json.dumps([sys.executable, str(tool), str(tmp_path)])
        + "\n"
    )
    return config, manifest, source


def cli(config, manifest, environment=None, *, retry_graph=False):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dogpdkb",
            "update-graph",
            "--config",
            str(config),
            "--input",
            str(manifest),
            *(["--retry-graph"] if retry_graph else []),
        ],
        env=environment or {**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_corpus_whitelist_uses_configured_nested_path(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "published/custom-book"
    corpus.mkdir(parents=True)
    ignore_file = tmp_path / ".graphifyignore"
    ignore_file.write_text("# Existing rule\nbuild/\n")

    _ensure_corpus_whitelist(corpus)
    _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == (
        b"!/published/\n"
        b"!/published/custom-book/\n"
        b"!/published/custom-book/**\n"
        b"# Existing rule\nbuild/\n"
    )


def test_corpus_whitelist_uses_existing_corpus_ignore_file(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "published/custom-book"
    corpus.mkdir(parents=True)
    ignore_file = corpus / ".graphifyignore"
    ignore_file.write_text("# Corpus-local rules\nignored.md\n")

    _ensure_corpus_whitelist(corpus)
    _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == b"!/**\n# Corpus-local rules\nignored.md\n"
    assert not (tmp_path / ".graphifyignore").exists()


def test_corpus_whitelist_skips_writes_without_git_rules(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    _ensure_corpus_whitelist(corpus)

    assert not (tmp_path / ".graphifyignore").exists()
    assert not (tmp_path / ".graphifyignore.lock").exists()


def test_corpus_whitelist_atomic_failure_preserves_existing_rules(
    tmp_path, monkeypatch
):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    original = b"# Existing rules\nignored.md\n"
    ignore_file.write_bytes(original)

    def fail_replace(source, destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr("dogpdkb.graph.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == original
    assert not [
        path
        for path in tmp_path.glob(".graphifyignore.*")
        if path.name != ".graphifyignore.lock"
    ]


def test_corpus_whitelist_rejects_pattern_syntax_before_writing(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "published/bad*book"
    corpus.mkdir(parents=True)
    ignore_file = tmp_path / ".graphifyignore"
    original = b"# Existing rules\nignored.md\n"
    ignore_file.write_bytes(original)

    with pytest.raises(ValueError, match="cannot be represented"):
        _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == original
    assert not (tmp_path / ".graphifyignore.lock").exists()


def test_corpus_whitelist_serializes_concurrent_books(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpora = []
    for index in range(8):
        corpus = tmp_path / f"corpus/book-{index}"
        corpus.mkdir(parents=True)
        corpora.append(corpus)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_ensure_corpus_whitelist, corpora))

    rules = (tmp_path / ".graphifyignore").read_text().splitlines()
    for index in range(8):
        assert rules.count(f"!/corpus/book-{index}/") == 1
        assert rules.count(f"!/corpus/book-{index}/**") == 1


def test_corpus_whitelist_supports_linked_worktree_exclude_file(tmp_path, monkeypatch):
    root = tmp_path / "root"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".git/info/exclude").write_text("*\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "--detach", str(linked)],
        check=True,
        capture_output=True,
    )
    corpus = linked / "corpus"
    corpus.mkdir()
    other = tmp_path / "other"
    subprocess.run(["git", "init", "--quiet", str(other)], check=True)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    _ensure_corpus_whitelist(corpus)

    assert (linked / ".graphifyignore").read_bytes() == b"!/corpus/\n!/corpus/**\n"


def test_corpus_whitelist_ignores_non_utf8_git_comment(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_bytes(b"# legacy \xff comment\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    _ensure_corpus_whitelist(corpus)

    assert not (tmp_path / ".graphifyignore").exists()
    assert not (tmp_path / ".graphifyignore.lock").exists()


def test_complete_corpus_whitelist_needs_no_lock_or_write(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    original = b"!/corpus/\n!/corpus/**\n# Existing rules\nignored.md\n"
    ignore_file.write_bytes(original)

    _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == original
    assert not (tmp_path / ".graphifyignore.lock").exists()


def test_corpus_whitelist_rejects_symlink_update(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    target = tmp_path / "shared-ignore"
    original = b"# Shared user rules\nignored.md\n"
    target.write_bytes(original)
    (tmp_path / ".graphifyignore").symlink_to(target)

    with pytest.raises(ValueError, match="symlinked"):
        _ensure_corpus_whitelist(corpus)

    assert target.read_bytes() == original
    assert not (tmp_path / ".graphifyignore.lock").exists()


def test_git_exclude_timeout_is_contextual(tmp_path, monkeypatch):
    (tmp_path / ".git").write_text("gitdir: /invalid\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git"], 10)

    monkeypatch.setattr("dogpdkb.graph.subprocess.run", timeout)
    with pytest.raises(ValueError, match="Git worktree exclude") as error:
        _ensure_corpus_whitelist(corpus)
    assert isinstance(error.value.__cause__, subprocess.TimeoutExpired)


def test_corpus_whitelist_preserves_crlf_and_existing_bytes(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    original = b"# Existing rules\r\nignored.md\r\n"
    ignore_file.write_bytes(original)

    _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == b"!/corpus/\n!/corpus/**\n" + original


def test_git_ignore_probe_handles_bom_and_first_column_comments(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".gitignore"
    ignore_file.write_bytes(b"\xef\xbb\xbf# comment only\n")

    _ensure_corpus_whitelist(corpus)
    assert not (tmp_path / ".graphifyignore").exists()

    ignore_file.write_bytes(b" #published/\n")
    _ensure_corpus_whitelist(corpus)
    assert (tmp_path / ".graphifyignore").is_file()


def test_git_ignore_probe_skips_non_regular_and_symlinked_paths(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".gitignore"
    ignore_file.mkdir()

    _ensure_corpus_whitelist(corpus)
    assert not (tmp_path / ".graphifyignore").exists()

    ignore_file.rmdir()
    target = tmp_path / "rules"
    target.write_text("*\n")
    ignore_file.symlink_to(target)
    _ensure_corpus_whitelist(corpus)
    assert not (tmp_path / ".graphifyignore").exists()

    ignore_file.unlink()
    os.mkfifo(ignore_file)
    _ensure_corpus_whitelist(corpus)
    assert not (tmp_path / ".graphifyignore").exists()


def test_git_exclude_path_uses_filesystem_decoding(tmp_path, monkeypatch):
    (tmp_path / ".git").write_text("gitdir: /unused\n")
    raw = os.fsencode(str(tmp_path)) + b"/repo-\xff/info/exclude"

    def resolved(*args, **kwargs):
        return subprocess.CompletedProcess(["git"], 0, stdout=raw + b"\n")

    monkeypatch.setattr("dogpdkb.graph.subprocess.run", resolved)
    result = _git_info_exclude(tmp_path)
    assert os.fsencode(result) == raw


def test_non_regular_graphifyignore_is_rejected_before_read(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    os.mkfifo(tmp_path / ".graphifyignore")

    with pytest.raises(ValueError, match="regular file"):
        _ensure_corpus_whitelist(corpus)

    assert not (tmp_path / ".graphifyignore.lock").exists()


def test_invalid_corpus_local_ignore_never_falls_back_to_root(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    root_rules = tmp_path / ".graphifyignore"
    original = b"# Root rules\nignored.md\n"
    root_rules.write_bytes(original)
    for kind in ("fifo", "dangling"):
        corpus = tmp_path / f"corpus-{kind}"
        corpus.mkdir()
        candidate = corpus / ".graphifyignore"
        if kind == "fifo":
            os.mkfifo(candidate)
        else:
            candidate.symlink_to(corpus / "missing")
        with pytest.raises(ValueError, match="regular file"):
            _ensure_corpus_whitelist(corpus)
        assert root_rules.read_bytes() == original


def test_local_git_rules_do_not_require_git_executable(tmp_path, monkeypatch):
    (tmp_path / ".git").write_text("gitdir: /unused\n")
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    def unexpected(root):
        raise AssertionError("info/exclude lookup is unnecessary")

    monkeypatch.setattr("dogpdkb.graph._git_info_exclude", unexpected)
    _ensure_corpus_whitelist(corpus)
    assert (tmp_path / ".graphifyignore").is_file()


def test_git_ignore_probe_uses_lf_and_significant_whitespace(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".gitignore"
    ignore_file.write_bytes(b"# note\r*\n")

    _ensure_corpus_whitelist(corpus)
    assert not (tmp_path / ".graphifyignore").exists()

    ignore_file.write_bytes(b"\t\n")
    _ensure_corpus_whitelist(corpus)
    assert (tmp_path / ".graphifyignore").is_file()


def test_git_exclude_relative_output_uses_worktree_root(tmp_path, monkeypatch):
    (tmp_path / ".git").write_text("gitdir: /unused\n")
    calls = []

    def resolved(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout=b"../shared/info/exclude\n"
        )

    monkeypatch.setattr("dogpdkb.graph.subprocess.run", resolved)
    result = _git_info_exclude(tmp_path)
    assert result == tmp_path / "../shared/info/exclude"
    assert "--path-format=absolute" not in calls[0]


def test_corpus_whitelist_aborts_replace_after_external_edit(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    ignore_file.write_bytes(b"# Original\nignored.md\n")
    external = b"# Operator edit\nnew-exclusion.md\n"
    real_fsync = os.fsync

    def edit_before_replace(fd):
        ignore_file.write_bytes(external)
        real_fsync(fd)

    monkeypatch.setattr("dogpdkb.graph.os.fsync", edit_before_replace)
    with pytest.raises(ValueError, match="changed during"):
        _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == external


def test_corpus_whitelist_preserves_ownership_and_xattrs(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    ignore_file.write_bytes(b"# Original\nignored.md\n")
    ignore_file.chmod(0o444)
    copied = []
    real_chown = os.chown

    def attributes(path, *, follow_symlinks):
        return ["user.fixture"] if Path(path) == ignore_file else []

    def read_attribute(path, name, *, follow_symlinks):
        assert (Path(path), name) == (ignore_file, "user.fixture")
        return b"attribute-value"

    def write_attribute(path, name, value, *, follow_symlinks):
        assert Path(path).stat().st_mode & 0o777 == 0o600
        copied.append((Path(path).name, name, value))

    def change_owner(path, uid, gid):
        copied.append((Path(path).name, uid, gid))
        real_chown(path, uid, gid)

    monkeypatch.setattr("dogpdkb.graph.os.listxattr", attributes)
    monkeypatch.setattr("dogpdkb.graph.os.getxattr", read_attribute)
    monkeypatch.setattr("dogpdkb.graph.os.setxattr", write_attribute)
    monkeypatch.setattr("dogpdkb.graph.os.chown", change_owner)
    _ensure_corpus_whitelist(corpus)

    stat = ignore_file.stat()
    assert any(
        entry[1:] == (stat.st_uid, stat.st_gid)
        for entry in copied
        if entry[0] != "user.fixture"
    )
    assert any(
        name == "user.fixture" and value == b"attribute-value"
        for _, name, value in copied
        if name == "user.fixture"
    )
    assert ignore_file.stat().st_mode & 0o777 == 0o444


def test_corpus_whitelist_clears_inherited_acl(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    ignore_file.write_bytes(b"# Original\nignored.md\n")
    removed = []

    def attributes(path, *, follow_symlinks):
        return [] if Path(path) == ignore_file else ["system.posix_acl_access"]

    def remove_attribute(path, name, *, follow_symlinks):
        removed.append((Path(path).name, name))

    monkeypatch.setattr("dogpdkb.graph.os.listxattr", attributes)
    monkeypatch.setattr("dogpdkb.graph.os.removexattr", remove_attribute)
    _ensure_corpus_whitelist(corpus)

    assert removed and all(name == "system.posix_acl_access" for _, name in removed)


def test_corpus_whitelist_chown_denied_preserves_original(
    tmp_path, monkeypatch, capsys
):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    original = b"# Original\nignored.md\n"
    ignore_file.write_bytes(original)

    def denied(path, uid, gid):
        raise PermissionError("EPERM")

    monkeypatch.setattr("dogpdkb.graph.os.chown", denied)
    _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == original
    assert "continuing with --no-gitignore" in capsys.readouterr().err


def test_corpus_whitelist_rejects_symlink_added_at_lock(tmp_path, monkeypatch):
    import fcntl

    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    ignore_file.write_bytes(b"# Original\nignored.md\n")
    target = tmp_path / "shared-rules"
    original_target = b"# Shared\nother.md\n"
    target.write_bytes(original_target)
    real_flock = fcntl.flock

    def replace_with_symlink(fd, operation):
        real_flock(fd, operation)
        ignore_file.unlink()
        ignore_file.symlink_to(target)

    monkeypatch.setattr("dogpdkb.graph.fcntl.flock", replace_with_symlink)
    with pytest.raises(ValueError, match="regular file"):
        _ensure_corpus_whitelist(corpus)

    assert ignore_file.is_symlink()
    assert target.read_bytes() == original_target


def test_corpus_whitelist_lock_denied_preserves_original(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    original = b"# Original\nignored.md\n"
    ignore_file.write_bytes(original)

    def denied(*args, **kwargs):
        raise PermissionError("EPERM")

    monkeypatch.setattr("dogpdkb.graph.os.open", denied)
    _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == original
    assert "continuing with --no-gitignore" in capsys.readouterr().err


def test_corpus_whitelist_detects_permission_change_before_replace(
    tmp_path, monkeypatch
):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    original = b"# Original\nignored.md\n"
    ignore_file.write_bytes(original)
    ignore_file.chmod(0o644)
    real_fsync = os.fsync

    def chmod_before_replace(fd):
        ignore_file.chmod(0o600)
        real_fsync(fd)

    monkeypatch.setattr("dogpdkb.graph.os.fsync", chmod_before_replace)
    with pytest.raises(ValueError, match="changed during"):
        _ensure_corpus_whitelist(corpus)

    assert ignore_file.stat().st_mode & 0o777 == 0o600
    assert ignore_file.read_bytes() == original


def test_corpus_whitelist_without_xattr_support_preserves_original(
    tmp_path, monkeypatch, capsys
):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    original = b"# Original\nignored.md\n"
    ignore_file.write_bytes(original)

    class WithoutXattrs:
        def __getattr__(self, name):
            if name == "listxattr":
                raise AttributeError(name)
            return getattr(os, name)

    monkeypatch.setattr("dogpdkb.graph.os", WithoutXattrs())
    _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == original
    assert "extended-attribute preservation is unavailable" in capsys.readouterr().err


def test_unreadable_git_ignore_warns_and_skips_maintenance(
    tmp_path, monkeypatch, capsys
):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".gitignore"
    ignore_file.write_text("*\n")
    real_read = Path.read_bytes

    def denied(path):
        if path == ignore_file:
            raise PermissionError("EPERM")
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", denied)
    _ensure_corpus_whitelist(corpus)

    assert not (tmp_path / ".graphifyignore").exists()
    assert "cannot read Git ignore source" in capsys.readouterr().err


def test_corpus_whitelist_update_permission_failure_preserves_original(
    tmp_path, monkeypatch, capsys
):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    ignore_file = tmp_path / ".graphifyignore"
    original = b"# Original\nignored.md\n"
    ignore_file.write_bytes(original)

    def denied(*args, **kwargs):
        raise PermissionError("EPERM")

    monkeypatch.setattr("dogpdkb.graph.tempfile.mkstemp", denied)
    _ensure_corpus_whitelist(corpus)

    assert ignore_file.read_bytes() == original
    assert "continuing with --no-gitignore" in capsys.readouterr().err


def test_graph_input_digest_uses_native_normalized_source_identity(tmp_path):
    config, manifest, source = fixture(tmp_path, title="Cafe\u0301")
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    graph = json.loads((tmp_path / "state/funds/state.json").read_bytes())["documents"][
        "42"
    ]["graph"]
    report = json.loads(Path(graph["report"]).read_bytes())
    assert graph["sha256"] == report["input_sha256"]["42"]
    assert graph["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize("budget", [None, 16000])
def test_complete_source_calls_only_public_graphify_and_records_success(
    tmp_path, budget
):
    config, manifest, source = fixture(tmp_path)
    if budget is not None:
        with config.open("a") as stream:
            stream.write(f"\n[graphify]\ntoken_budget = {budget}\n")
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert [row[0] for row in calls] == ["extract"]
    assert Path(calls[0][1]) == source.parent
    assert Path(calls[0][calls[0].index("--out") + 1]) == source.parent
    report = json.loads(Path(json.loads(result.stdout)["report"]).read_text())
    assert "candidate" not in report
    assert (Path(calls[0][1]) / source.name).read_bytes() == source.read_bytes()
    assert "--no-cluster" in calls[0]
    assert "--no-gitignore" in calls[0]
    if budget is None:
        assert "--token-budget" not in calls[0]
    else:
        assert calls[0][-2:] == ["--token-budget", str(budget)]
    state = json.loads((tmp_path / "state/funds/state.json").read_text())["documents"][
        "42"
    ]
    assert state["graph"]["version"] == VERSION
    assert state["steps"]["graph"]["status"] == "completed"
    assert not state.get("pending")


@pytest.mark.parametrize("budget", ['"512"', "true", "512.0", "0", "-1"])
def test_invalid_graphify_token_budget_fails_before_extraction(tmp_path, budget):
    config, manifest, _ = fixture(tmp_path)
    with config.open("a") as stream:
        stream.write(f"\n[graphify]\ntoken_budget = {budget}\n")

    result = cli(config, manifest)

    assert result.returncode == 1
    assert "graphify.token_budget: expected a positive integer" in result.stderr
    assert not list(manifest.parent.glob("graphify-*"))


def test_source_failure_keeps_graph_version_and_is_not_retried(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    old = {
        "status": "completed",
        "version": {"utc": "2026-11-10T10:00:00+00:00"},
        "source_file": "old.md",
    }
    data["documents"]["42"]["graph"] = old
    write(state_path, data)
    (tmp_path / "mode").write_text("failed")
    first = cli(config, manifest)
    assert first.returncode == 2, first.stdout + first.stderr
    doc = json.loads(state_path.read_text())["documents"]["42"]
    assert doc["graph"] == old
    assert doc["corpus"]["version"] == VERSION
    assert doc["steps"]["graph"]["status"] == "failed"
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest).returncode == 2
    assert (tmp_path / "calls.jsonl").read_bytes() == before


@pytest.mark.parametrize(
    "mode,code", [("failed", 2), ("invalid", 1), ("interrupt", 130)]
)
def test_explicit_graph_retry_recovers_same_run_and_keeps_failure_evidence(
    tmp_path, mode, code
):
    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text(mode)
    assert cli(config, manifest).returncode == code
    run_path = manifest.parent / "run.json"
    before = json.loads(run_path.read_text())
    report = Path(before["artifacts"]["graph_report"])
    evidence = report.read_bytes()
    (tmp_path / "mode").write_text("success")

    result = cli(config, manifest, retry_graph=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 2
    assert report.read_bytes() == evidence
    after = json.loads(run_path.read_text())
    assert after["failures"] == before["failures"]
    assert after["artifacts"]["graph_report"] != str(report)
    document = json.loads((tmp_path / "state/funds/state.json").read_text())[
        "documents"
    ]["42"]
    assert document["graph"]["status"] == "completed"
    assert document["steps"]["graph"]["status"] == "completed"
    assert not document["blocked_versions"]["graph"]
    assert (source.parent / "graphify-out/graph.json").is_file()


def test_graph_retry_refuses_another_runs_failure(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    doc = state["documents"]["42"]
    doc["steps"]["graph"]["run_id"] = "another-run"
    doc["blocked_versions"]["graph"][VERSION["utc"]]["run_id"] = "another-run"
    write(state_path, state)
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest, retry_graph=True).returncode == 2
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_graph_retry_refuses_changed_source_before_tool_call(tmp_path):
    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    source.write_text("Changed after failure")
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest, retry_graph=True).returncode == 1
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_graph_retry_does_not_authorize_unselected_blocked_sources(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    held = tmp_path / "corpus/held.md"
    held.write_text("# Held Corpus body\n")
    state["documents"]["43"] = {
        "corpus": {"version": VERSION, "path": str(held)},
        "steps": {
            "graph": {
                "status": "failed",
                "version": VERSION,
                "run_id": manifest.parent.name,
            }
        },
    }
    write(state_path, state)
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest, retry_graph=True).returncode == 2
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_graph_retry_cannot_bypass_target_lock(tmp_path):
    import fcntl

    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text("failed")
    assert cli(config, manifest).returncode == 2
    before = (tmp_path / "calls.jsonl").read_bytes()
    with (source.parent / ".dogpdkb.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = cli(config, manifest, retry_graph=True)
    assert result.returncode == 1
    assert "another writer" in result.stderr
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_graph_retry_recovers_running_attempt_after_owner_exits(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["42"]["steps"]["graph"] = {
        "status": "running",
        "version": VERSION,
        "run_id": manifest.parent.name,
    }
    write(state_path, state)
    result = cli(config, manifest, retry_graph=True)
    assert result.returncode == 0, result.stderr


def test_invalid_report_is_unknown_and_does_not_advance_graph_truth(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    (tmp_path / "mode").write_text("invalid")
    result = cli(config, manifest)
    assert result.returncode == 1, result.stdout + result.stderr
    doc = json.loads((tmp_path / "state/funds/state.json").read_text())["documents"][
        "42"
    ]
    assert "graph" not in doc
    assert doc["steps"]["graph"]["status"] == "unknown"
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest).returncode == 2
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_changed_source_refuses_old_manifest_before_tool_call(tmp_path):
    config, manifest, source = fixture(tmp_path)
    source.write_text("Changed after planning")
    result = cli(config, manifest)
    assert result.returncode == 1
    assert "generate a new plan" in result.stderr
    assert not (tmp_path / "calls.jsonl").exists()


def test_resuming_a_completed_graph_does_not_repeat_tool_work(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    assert cli(config, manifest).returncode == 0
    before = (tmp_path / "calls.jsonl").read_bytes()
    assert cli(config, manifest).returncode == 0
    assert (tmp_path / "calls.jsonl").read_bytes() == before


def test_success_keeps_a_newer_discovery_pending(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    pending = {
        "version": {"utc": "2026-11-13T10:00:00+00:00"},
        "metadata": {"name": "New title"},
    }
    data["documents"]["42"]["pending"] = pending
    write(state_path, data)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(state_path.read_text())["documents"]["42"]["pending"] == pending


def test_failed_directory_batch_advances_no_source(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    second = dict(
        data["documents"][0],
        document_id="43",
        path=str(tmp_path / "corpus/Second-2026-11-12-[43].md"),
        input_copy=str(manifest.parent / "published/1.md"),
    )
    Path(second["path"]).write_text("# Full second source\n")
    Path(second["input_copy"]).write_text("# Full second source\n")
    data["documents"].append(second)
    write(manifest, data)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    old = {
        "status": "completed",
        "version": {"utc": "2026-11-11T10:00:00+00:00"},
        "source_file": "prior.md",
    }
    state["documents"]["43"] = {
        "steps": {},
        "graph": old,
        "corpus": {
            "path": second["path"],
            "version": VERSION,
            "input_copy": second["input_copy"],
            "metadata": second["metadata"],
        },
    }
    write(state_path, state)
    (tmp_path / "mode").write_text("partial")
    result = cli(config, manifest)
    assert result.returncode == 2, result.stdout + result.stderr
    documents = json.loads(state_path.read_text())["documents"]
    assert "graph" not in documents["42"]
    assert documents["42"]["steps"]["graph"]["status"] == "failed"
    assert documents["43"]["graph"] == old
    assert documents["43"]["steps"]["graph"]["status"] == "failed"


def test_interrupt_marks_directory_batch_unknown(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    second = dict(
        data["documents"][0],
        document_id="43",
        path=str(tmp_path / "corpus/Second.md"),
        input_copy=str(manifest.parent / "published/1.md"),
    )
    Path(second["path"]).write_text("# Second full source\n")
    Path(second["input_copy"]).write_text("# Second full source\n")
    data["documents"].append(second)
    write(manifest, data)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["43"] = {
        "steps": {},
        "pending": {"version": VERSION, "metadata": second["metadata"]},
        "corpus": {
            "path": second["path"],
            "version": VERSION,
            "input_copy": second["input_copy"],
            "metadata": second["metadata"],
        },
    }
    write(state_path, state)
    (tmp_path / "mode").write_text("interrupt")
    result = cli(config, manifest)
    assert result.returncode == 130, result.stdout + result.stderr
    documents = json.loads(state_path.read_text())["documents"]
    assert documents["42"]["steps"]["graph"]["status"] == "unknown"
    assert documents["43"]["steps"]["graph"]["status"] == "unknown"
    assert documents["43"]["pending"]["version"] == VERSION
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert len(calls) == 1


def test_old_manifest_cannot_replace_a_newer_successful_graph(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    graph = {"status": "completed", "version": {"utc": "2026-11-13T10:00:00+00:00"}}
    data["documents"]["42"]["graph"] = graph
    write(state_path, data)
    result = cli(config, manifest)
    assert result.returncode == 1
    assert "newer graph version exists" in result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    assert json.loads(state_path.read_text())["documents"]["42"]["graph"] == graph


@pytest.mark.parametrize("current_graph, expected", [("v2", 0), ("v11", 1)])
def test_opaque_graph_versions_use_frozen_success_facts(
    tmp_path, current_graph, expected
):
    config, manifest, _source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["documents"][0].update(
        version={"value": "v10"},
        previous_graph={"status": "completed", "version": {"value": "v2"}},
    )
    write(manifest, data)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    document = state["documents"]["42"]
    document["corpus"]["version"] = {"value": "v10"}
    document["graph"] = {
        "status": "completed",
        "version": {"value": current_graph},
    }
    write(state_path, state)
    result = cli(config, manifest)
    assert result.returncode == expected, result.stdout + result.stderr
    if expected == 0:
        assert json.loads(state_path.read_text())["documents"]["42"]["graph"][
            "version"
        ] == {"value": "v10"}
    else:
        assert "generate a new plan" in result.stderr
        assert not (tmp_path / "calls.jsonl").exists()


def test_graph_success_keeps_never_started_ocr_pending(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["documents"][0]["pending_steps"] = ["ocr"]
    write(manifest, data)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads((tmp_path / "state/funds/state.json").read_text())[
        "documents"
    ]["42"]
    assert document["graph"]["version"] == VERSION
    assert document["pending"]["version"] == VERSION


def test_changed_input_in_same_run_and_version_is_compiled_again(tmp_path):
    config, manifest, source = fixture(tmp_path)
    assert cli(config, manifest).returncode == 0
    before = (tmp_path / "calls.jsonl").read_text().splitlines()
    source.write_text("Full source with newly completed OCR.\n")
    data = json.loads(manifest.read_text())
    Path(data["documents"][0]["input_copy"]).write_bytes(source.read_bytes())
    assert cli(config, manifest).returncode == 0
    calls = (tmp_path / "calls.jsonl").read_text().splitlines()
    assert len(calls) == len(before) + 1


def test_unknown_pending_version_does_not_lose_acknowledged_graph_success(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    data = json.loads(state_path.read_text())
    data["documents"]["42"]["pending"]["version"] = None
    write(state_path, data)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(state_path.read_text())["documents"]["42"]
    assert document["graph"]["version"] == VERSION
    assert document["pending"]["version"] is None


def test_superseded_metadata_manifest_is_refused(tmp_path):
    config, manifest, _source = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    state["documents"]["42"]["corpus"]["metadata"] = {"logical_path": "New folder"}
    write(state_path, state)
    result = cli(config, manifest)
    assert result.returncode == 1
    assert "generate a new plan" in result.stderr
    assert not (tmp_path / "calls.jsonl").exists()


def test_failed_native_write_leaves_corpus_and_does_not_acknowledge(tmp_path):
    config, manifest, source = fixture(tmp_path)
    before = source.read_bytes()
    graph = source.parent / "graphify-out/graph.json"
    write(graph, {"nodes": [{"id": "old"}], "links": []})
    (tmp_path / "mode").write_text("failed")
    result = cli(config, manifest)
    assert result.returncode == 2
    assert json.loads(graph.read_bytes())["nodes"][0]["id"] == "unpublished"
    assert source.read_bytes() == before
    run = json.loads((manifest.parent / "run.json").read_text())
    assert run["graph_publication"] == "unknown"
    assert run["graph_result"] == "failed"
    document = json.loads((tmp_path / "state/funds/state.json").read_text())[
        "documents"
    ]["42"]
    assert "graph" not in document


def test_native_partial_result_does_not_acknowledge_and_suppresses(tmp_path):
    config, manifest, source = fixture(tmp_path)
    graph = source.parent / "graphify-out/graph.json"
    native_manifest = graph.with_name("manifest.json")
    cache = graph.parent / "cache/entry"
    write(graph, {"nodes": [{"id": "old"}], "links": []})
    write(native_manifest, {})
    cache.parent.mkdir()
    cache.write_bytes(b"previous trusted entry")
    generation = [graph, native_manifest, cache]
    before = [path.read_bytes() for path in generation]
    (tmp_path / "mode").write_text("grow-incomplete")

    first = cli(config, manifest)
    assert first.returncode == 2, first.stdout + first.stderr
    assert graph.read_bytes() != before[0]
    assert cache.read_bytes() == b"unpublishable"
    assert (
        "graph"
        not in json.loads((tmp_path / "state/funds/state.json").read_text())[
            "documents"
        ]["42"]
    )
    assert cli(config, manifest).returncode == 2
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 1

    (tmp_path / "mode").write_text("success")
    result = cli(config, manifest, retry_graph=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(path.read_bytes() != old for path, old in zip(generation, before))
    assert cache.read_bytes() == b"trusted replacement"


def test_missing_chunk_completion_is_not_proof_even_with_current_stamps(tmp_path):
    config, manifest, source = fixture(tmp_path)
    tool = tmp_path / "graphify.py"
    tool.write_text(
        tool.read_text().replace(
            "    print('[graphify extract] chunk 1/1 done')\n",
            "",
        )
    )

    first = cli(config, manifest)
    assert first.returncode == 2, first.stdout + first.stderr
    assert (source.parent / "graphify-out/graph.json").exists()
    assert cli(config, manifest).returncode == 2
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("edge_key", ["edges", "links"])
def test_native_edge_source_metadata_is_left_untouched(tmp_path, edge_key):
    config, manifest, source = fixture(tmp_path)
    tool = tmp_path / "graphify.py"
    edge = {"source": source.name, "target": source.name, "source_file": "native-label"}
    write_graph = "(out/'graph.json').write_text(json.dumps(graph))"
    proof = GRAPHIFY_PROOF.replace(
        write_graph,
        f"graph[{edge_key!r}] = [{edge!r}]\n" + write_graph,
    )
    tool.write_text(tool.read_text().removesuffix(GRAPHIFY_PROOF) + proof)

    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr
    graph = source.parent / "graphify-out/graph.json"
    assert json.loads(graph.read_bytes())[edge_key] == [edge]


@pytest.mark.parametrize("native_cache", [False, True])
def test_no_dispatch_accepts_proven_baseline_or_cache_coverage(tmp_path, native_cache):
    config, manifest, source = fixture(tmp_path)
    tool = tmp_path / "graphify.py"
    proof = GRAPHIFY_PROOF[: GRAPHIFY_PROOF.index("if manifest:")]
    if native_cache:
        proof += "print('[graphify extract] semantic cache: 1 hit / 0 miss')\n"
    else:
        graph = source.parent / "graphify-out/graph.json"
        # Graphify's native change stamp is MD5, not a security digest.
        stamp = hashlib.new(
            "md5", source.read_bytes(), usedforsecurity=False
        ).hexdigest()
        write(graph.with_name("manifest.json"), {source.name: {"semantic_hash": stamp}})
        write(
            graph, {"nodes": [{"id": "old", "source_file": source.name}], "links": []}
        )
    proof += "print(f'[graphify extract] wrote {out / \"graph.json\"} — complete')\n"
    tool.write_text(tool.read_text().removesuffix(GRAPHIFY_PROOF) + proof)
    result = cli(config, manifest)
    assert result.returncode == 0, result.stdout + result.stderr


def test_changed_input_during_extract_does_not_acknowledge(tmp_path):
    config, manifest, source = fixture(tmp_path)
    (tmp_path / "mode").write_text("mutate")
    result = cli(config, manifest)
    assert result.returncode == 1, result.stderr
    assert (source.parent / "graphify-out/graph.json").is_file()
    document = json.loads((tmp_path / "state/funds/state.json").read_text())[
        "documents"
    ]["42"]
    assert "graph" not in document


def test_original_graphify_incremental_directory_preserves_other_sources(tmp_path):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    executable = shutil.which("graphify")
    if not executable:
        pytest.skip("original Graphify CLI is not installed")
    config, manifest, source = fixture(tmp_path)
    kept = source.with_name("Unchanged-[99].md")
    kept.write_text("# Unchanged beta source\nBeta stays unchanged.\n")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/codex/responses"
            assert self.headers["chatgpt-account-id"] == "offline-fixture"
            body = request["input"][0]["content"][0]["text"]
            calls.append(body)
            nodes = []
            for name in (source.name, kept.name):
                if name in body:
                    ident = (
                        "kept"
                        if name == kept.name
                        else "fresh"
                        if "Changed alpha" in body
                        else "old"
                    )
                    nodes.append(
                        {
                            "id": ident,
                            "label": ident,
                            "file_type": "concept",
                            "source_file": name,
                        }
                    )
            result = graphify_sse(nodes)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = graphify_offline_env(tmp_path, server.server_port)
    try:
        before = subprocess.run(
            [
                executable,
                "extract",
                str(source.parent),
                "--no-cluster",
                "--backend",
                "openai-oauth",
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert before.returncode == 0, before.stdout + before.stderr
        source.write_text("# Changed alpha\nThis is the revised full alpha source.\n")
        data = json.loads(manifest.read_text())
        Path(data["documents"][0]["input_copy"]).write_bytes(source.read_bytes())
        config.write_text(
            config.read_text().split("[tools]")[0]
            + "[tools]\ngraphify="
            + json.dumps(executable)
            + "\n"
        )
        result = cli(config, manifest, environment)
        assert result.returncode == 0, result.stdout + result.stderr
        graph = json.loads((source.parent / "graphify-out/graph.json").read_text())
        assert {n["id"] for n in graph["nodes"]} == {"kept", "fresh"}
        assert len(calls) == 2
        assert source.name in calls[1]
        assert kept.name not in calls[1]
        count = len(calls)
        assert cli(config, manifest, environment).returncode == 0
        assert len(calls) == count
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("git_exclude", "git_pattern", "corpus_path", "whitelist"),
    [
        (
            ".gitignore",
            "*",
            "corpus",
            "!/corpus/\n!/corpus/**\n",
        ),
        (
            ".git/info/exclude",
            "*",
            "corpus",
            "!/corpus/\n!/corpus/**\n",
        ),
        (
            ".gitignore",
            "published/custom-book/",
            "published/custom-book",
            "!/published/\n!/published/custom-book/\n!/published/custom-book/**\n",
        ),
        (
            ".git/info/exclude",
            "published/",
            "published/custom-book",
            "!/published/\n!/published/custom-book/\n!/published/custom-book/**\n",
        ),
        (
            ".git/info/exclude",
            "published/custom-*/",
            "published/custom-book",
            "!/published/\n!/published/custom-book/\n!/published/custom-book/**\n",
        ),
        (
            ".gitignore",
            "published/custom-book/Title*",
            "published/custom-book",
            "!/published/\n!/published/custom-book/\n!/published/custom-book/**\n",
        ),
    ],
)
def test_installed_graphify_bypasses_git_exclusions_and_preserves_ignore_rules(
    tmp_path, git_exclude, git_pattern, corpus_path, whitelist
):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    executable = shutil.which("graphify")
    if not executable:
        pytest.skip("patched Graphify CLI is not installed")
    config, manifest, source = fixture(tmp_path, corpus_path=corpus_path)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / git_exclude).write_text(git_pattern + "\n")
    ignored = source.with_name("ignored.md")
    ignored.write_text("# Explicitly excluded by Graphify rules\n")
    ignore_file = tmp_path / ".graphifyignore"
    ignore_file.write_text("# Retain user rules\nignored.md\n")
    config.write_text(
        config.read_text().split("[tools]")[0]
        + "[tools]\ngraphify="
        + json.dumps(executable)
        + "\n"
    )
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = request["input"][0]["content"][0]["text"]
            calls.append(body)
            assert source.name in body
            assert ignored.name not in body
            result = graphify_sse(
                [
                    {
                        "id": "current",
                        "label": "current",
                        "source_file": source.name,
                        "file_type": "concept",
                    }
                ]
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = graphify_offline_env(tmp_path, server.server_port)
    try:
        result = cli(config, manifest, environment)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(Path(json.loads(result.stdout)["report"]).read_text())
        assert "--no-gitignore" in report["argv"]
        graph = json.loads((source.parent / "graphify-out/graph.json").read_text())
        assert source.name in {n.get("source_file") for n in graph["nodes"]}
        assert ignored.name not in {n.get("source_file") for n in graph["nodes"]}
        expected = (whitelist + "# Retain user rules\nignored.md\n").encode()
        assert ignore_file.read_bytes() == expected

        # A real second dispatch preserves existing rules and adds no duplicate.
        source.write_text("# Changed source\n\nCurrent full Markdown.\n")
        data = json.loads(manifest.read_text())
        Path(data["documents"][0]["input_copy"]).write_bytes(source.read_bytes())
        assert cli(config, manifest, environment).returncode == 0
        assert ignore_file.read_bytes() == expected
        assert len(calls) == 2
    finally:
        server.shutdown()
        server.server_close()


def test_installed_graphify_budget_recovers_omissions_with_three_inflight(tmp_path):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    executable = shutil.which("graphify")
    if not executable:
        pytest.skip("patched Graphify CLI is not installed")
    config, manifest, source = fixture(tmp_path)
    corpus = source.parent
    batch = json.loads(manifest.read_text())
    first = batch["documents"][0]
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_text())
    for index in range(5):
        doc_id = str(42 + index)
        path = corpus / f"source-{index}.md" if index else source
        witness = manifest.parent / f"published/{index}.md"
        path.write_text(f"# {path.name}\n\n" + "knowledge topic sentence. " * 120)
        witness.write_bytes(path.read_bytes())
        if index:
            batch["documents"].append(
                {
                    **first,
                    "document_id": doc_id,
                    "path": str(path),
                    "input_copy": str(witness),
                }
            )
            state["documents"][doc_id] = {
                **state["documents"]["42"],
                "corpus": {
                    **state["documents"]["42"]["corpus"],
                    "path": str(path),
                    "input_copy": str(witness),
                },
            }
    write(manifest, batch)
    write(state_path, state)
    config.write_text(
        config.read_text().split("[tools]")[0]
        + "[tools]\ngraphify="
        + json.dumps(executable)
        + "\n"
    )
    names = {Path(row["path"]).name for row in batch["documents"]}
    condition = threading.Condition()
    dispatched = []
    models = []
    peak = active = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            nonlocal peak, active
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = request["input"][0]["content"][0]["text"]
            found = {name for name in names if name in body}
            with condition:
                dispatched.append(found)
                models.append(request["model"])
                active += 1
                peak = max(peak, active)
                condition.notify_all()
                if len(found) == 1 and condition.wait_for(lambda: peak >= 3, timeout=1):
                    condition.wait_for(lambda: peak > 3, timeout=0.2)
                active -= 1
            # A well-formed response that omits files only when batched together.
            result = graphify_sse(
                [
                    {
                        "id": name,
                        "label": name,
                        "source_file": name,
                        "file_type": "concept",
                    }
                    for name in sorted(found)[:1]
                ]
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        environment = graphify_offline_env(tmp_path, server.server_port)
        result = cli(config, manifest, environment)
        assert result.returncode == 2, result.stdout + result.stderr
        assert dispatched == [names]
        failed = json.loads(
            next(
                (tmp_path / "state/funds/graph-evidence").glob("graphify-*/result.json")
            ).read_text()
        )
        assert failed["exit_code"] == 0
        assert "GRAPHIFY_INCOMPLETE_EXTRACTION" in failed["reason"]
        log = Path(failed["native_log"]).read_text()
        assert "chunk 1/1 done" in log
        assert (
            "4 dispatched file(s) produced no nodes and 0 came back truncated or hollow"
            in log
        )
        public_graph = corpus / "graphify-out/graph.json"
        # Native extraction writes in place; dogpdkb withholds acknowledgement.
        assert public_graph.is_file()
        assert all(
            not row.get("graph")
            for row in json.loads(state_path.read_text())["documents"].values()
        )
        assert cli(config, manifest, environment).returncode == 2
        assert dispatched == [names]  # same-version suppression still applies

        with config.open("a") as stream:
            stream.write('\n[graphify]\ntoken_budget = 512\nmodel = "gpt-6.1-terra"\n')
        dispatched.clear()
        models.clear()
        peak = 0
        result = cli(config, manifest, environment, retry_graph=True)
        assert result.returncode == 0, result.stdout + result.stderr
        omitted = names - {source.name}
        assert len(dispatched) == len(omitted)
        assert set.union(*dispatched) == omitted
        assert peak == 3
        assert models == ["gpt-6.1-terra"] * len(omitted)
        assert {
            n["source_file"] for n in json.loads(public_graph.read_text())["nodes"]
        } == names
        documents = json.loads(state_path.read_text())["documents"]
        assert all(
            row["steps"]["graph"]["status"] == "completed" for row in documents.values()
        )
        report = json.loads(Path(documents["42"]["graph"]["report"]).read_text())
        assert report["graphify_model"] == "gpt-6.1-terra"
        assert report["graphify_token_budget"] == 512
        assert "chunk 4/4 done" in Path(report["native_log"]).read_text()
    finally:
        server.shutdown()
        server.server_close()


def test_usable_corpus_compiles_despite_separate_apply_failures(tmp_path):
    config, manifest, _ = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["failures"] = [{"document_id": "43", "step": "ocr", "reason": "fixture"}]
    write(manifest, data)
    result = cli(config, manifest)
    assert result.returncode == 2, result.stdout + result.stderr
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert state["documents"]["42"]["graph"]["version"] == VERSION


@pytest.mark.parametrize("action,code", [("retain", 0), ("defer", 2)])
def test_only_retained_sources_do_not_call_graphify(tmp_path, action, code):
    config, manifest, _ = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["documents"][0]["action"] = action
    write(manifest, data)

    result = cli(config, manifest)

    assert result.returncode == code, result.stdout + result.stderr
    assert not (tmp_path / "calls.jsonl").exists()
    state = json.loads((tmp_path / "state/funds/state.json").read_text())
    assert "graph" not in state["documents"]["42"]["steps"]


def test_unselected_corpus_exposure_blocks_next_run_and_is_not_acknowledged(tmp_path):
    config, manifest, selected = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_bytes())
    unselected = selected.parent / "unselected.md"
    unselected.write_text("# Published by another Corpus batch\n")
    state["documents"]["43"] = {"corpus": {"version": VERSION, "path": str(unselected)}}
    write(state_path, state)
    mode = tmp_path / "mode"
    mode.write_text("interrupt")
    assert cli(config, manifest).returncode == 130
    state = json.loads(state_path.read_bytes())
    doc = state["documents"]["43"]
    assert doc["steps"]["graph"]["status"] == "unknown"
    assert doc["steps"]["graph"]["scan_only"]
    assert "graph" not in doc

    # A new version of the selected source does not authorize resending 43.
    next_manifest = manifest.parent.with_name("run-next") / "manifest.json"
    request = json.loads(manifest.read_bytes())
    new_version = {**VERSION, "utc": "2026-11-13T10:00:00+00:00"}
    request["run_id"] = "run-next"
    request["documents"][0]["version"] = new_version
    write(next_manifest, request)
    run = json.loads((manifest.parent / "run.json").read_bytes())
    run.update(
        run_id="run-next", artifacts={"manifest": str(next_manifest)}, failures=[]
    )
    write(next_manifest.parent / "run.json", run)
    state["documents"]["42"]["corpus"]["version"] = new_version
    write(state_path, state)
    mode.write_text("success")
    assert cli(config, next_manifest).returncode == 2
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 1

    state["documents"]["42"]["corpus"]["version"] = VERSION
    write(state_path, state)
    original = unselected.read_bytes()
    unselected.write_text("# Same-version enrichment from another batch\n")
    changed = cli(config, manifest, retry_graph=True)
    assert (
        changed.returncode == 1 and "saved Graphify attempt changed" in changed.stderr
    )
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 1
    unselected.write_bytes(original)
    assert cli(config, manifest, retry_graph=True).returncode == 0
    doc = json.loads(state_path.read_bytes())["documents"]["43"]
    assert "graph" not in doc
    assert doc["steps"]["graph"]["status"] == "completed"
    assert not doc["blocked_versions"]["graph"]


def test_unselected_unsettled_corpus_publication_stops_native_send(tmp_path):
    config, manifest, selected = fixture(tmp_path)
    state_path = tmp_path / "state/funds/state.json"
    state = json.loads(state_path.read_bytes())
    source = selected.parent / "unsettled.md"
    source.write_text("# Replaced before Corpus facts were saved\n")
    prior = hashlib.sha256(b"# Older published body\n").hexdigest()
    state["documents"]["43"] = {
        "corpus": {"version": VERSION, "path": str(source), "input_copy_sha256": prior},
        "graph": {"status": "completed", "version": VERSION, "sha256": prior},
    }
    write(state_path, state)
    assert cli(config, manifest).returncode == 2
    run = json.loads((manifest.parent / "run.json").read_bytes())
    assert run["failures"][-1]["reason"] == "CORPUS_PUBLICATION_UNSETTLED"
    assert not (tmp_path / "calls.jsonl").exists()
