"""Recovery through the read-only MCP adapter and fresh listing boundary."""

import json
import os
import select
import signal
import subprocess
import sys
import time

import pytest

from scripts import get_sync_updates as sync


def scripted_proxy(tmp_path, monkeypatch, outcomes):
    """Serve scripted wire responses, recording requests across proxy attempts."""
    plan = tmp_path / "responses.json"
    calls = tmp_path / "calls.jsonl"
    plan.write_text(json.dumps(outcomes))
    proxy = tmp_path / "proxy"
    proxy.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "from pathlib import Path\n"
        f"plan=Path({str(plan)!r}); calls=Path({str(calls)!r})\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'id':1,'result':{}}),flush=True)\n"
        "sys.stdin.readline()\n"
        "request=json.loads(sys.stdin.readline())\n"
        "with calls.open('a') as stream: stream.write(json.dumps(request['params'])+'\\n')\n"
        "outcomes=json.loads(plan.read_text()); outcome=outcomes.pop(0); plan.write_text(json.dumps(outcomes))\n"
        "if outcome.get('disconnect'): sys.exit(0)\n"
        "if outcome.get('partial'): sys.stdout.write('{'); sys.stdout.flush(); __import__('time').sleep(5)\n"
        "if 'mcp_error' in outcome: reply={'id':2,'result':{'isError':True,'content':[{'text':outcome['mcp_error']}]}}\n"
        "elif 'rpc_error' in outcome: reply={'id':2,'error':outcome['rpc_error']}\n"
        "else: reply={'id':2,'result':{'content':[{'text':json.dumps(outcome)}]}}\n"
        "print(json.dumps(reply),flush=True)\n"
    )
    proxy.chmod(0o755)
    monkeypatch.setattr(sync, "proxy_bin", lambda: str(proxy))
    monkeypatch.setattr(sync, "max_retries", lambda: 2)
    monkeypatch.setattr(sync.time, "sleep", lambda delay: None)
    return calls


def test_production_timeout_retries_page_without_restarting_listing(
    tmp_path, monkeypatch, capsys
):
    # Serialize pages so the wire script stays deterministic, independent of threads.
    monkeypatch.setattr(sync, "PAGE_SIZE", 2)
    from types import SimpleNamespace

    monkeypatch.setattr(sync, "current_config", lambda: SimpleNamespace(concurrency=1))
    monkeypatch.setattr(sync, "server_url", lambda: "https://example.invalid/mcp")
    monkeypatch.setattr(sync, "timeout_s", lambda: 2)
    calls = scripted_proxy(
        tmp_path,
        monkeypatch,
        [
            {"ok": True, "data": [{"id": 1}, {"id": 2}]},
            {"ok": False, "error_msg": "Request timeout for 15000 ms"},
            {"ok": True, "data": [{"id": 3}]},
        ],
    )
    assert sync.fetch_doc_list(123, 3, tmp_path / "pages") == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]
    assert [
        json.loads(line)["arguments"]["offset"]
        for line in calls.read_text().splitlines()
    ] == [0, 2, 2]
    output = capsys.readouterr().err
    assert "book_id=123" in output and "offset=2" in output
    assert "remote" in output and "15000 ms" in output
    assert "retry 1/2" in output and "wait=" in output


@pytest.mark.parametrize(
    "failure",
    [
        {"ok": False, "details": {"statusCode": 502}},
        {"ok": False, "details": {"status_code": "503"}},
        {"ok": False, "status": 504},
        {"ok": False, "status": 408},
        {"ok": False, "error_code": "ECONNRESET"},
        {"ok": False, "retryable": True},
        {"rpc_error": {"code": -32000, "message": "Request timeout for 15000 ms"}},
        {"rpc_error": {"code": -32000, "data": {"statusCode": 429}}},
        {"mcp_error": "Request timeout for 15000 ms"},
    ],
)
def test_temporary_wire_errors_share_retry_budget(tmp_path, monkeypatch, failure):
    calls = scripted_proxy(tmp_path, monkeypatch, [failure, {"ok": True, "data": []}])
    monkeypatch.setattr(sync, "server_url", lambda: "https://example.invalid/mcp")
    assert sync.call_tool("skylark_book_toc", {"book_id": 7}) == []
    assert len(calls.read_text().splitlines()) == 2


@pytest.mark.parametrize(
    "failure",
    [
        {"ok": False, "retryable": True, "details": {"statusCode": 401}},
        {"ok": False, "error_code": "PERMISSION_DENIED", "retryable": True},
        {"ok": False, "error_code": "TOKEN_EXPIRED", "retryable": True},
        {"ok": False, "error_code": "INVALID_ARGUMENT", "details": {"statusCode": 503}},
        {"ok": False, "message": "Permission denied", "retryable": True},
        {"ok": False, "message": "unknown timeout while parsing stored text"},
        {
            "rpc_error": {
                "code": -32602,
                "message": "invalid params",
                "data": {"retryable": True},
            }
        },
    ],
)
def test_permanent_or_unknown_wire_errors_fail_without_retry(
    tmp_path, monkeypatch, failure
):
    calls = scripted_proxy(tmp_path, monkeypatch, [failure])
    monkeypatch.setattr(sync, "server_url", lambda: "https://example.invalid/mcp")
    with pytest.raises(sync.SkylarkError) as error:
        sync.call_tool("skylark_doc_detail", {"doc_id": 17})
    assert not error.value.retryable
    assert len(calls.read_text().splitlines()) == 1
    assert "doc_id=17" in str(error.value)
    assert "attempt=1/3" in str(error.value)


def test_exhausted_budget_is_bounded_and_hides_response_credentials(
    tmp_path, monkeypatch, capsys
):
    failure = {
        "ok": False,
        "error_code": "UPSTREAM_RETRYABLE",
        "message": "token=topsecret body=" + "sensitive content" * 100,
    }
    calls = scripted_proxy(tmp_path, monkeypatch, [failure, failure, failure])
    monkeypatch.setattr(sync, "server_url", lambda: "https://example.invalid/mcp")
    with pytest.raises(sync.SkylarkError) as error:
        sync.call_tool("skylark_doc_detail", {"doc_id": 17})
    assert len(calls.read_text().splitlines()) == 3
    assert "attempt=3/3" in str(error.value)
    assert error.value.code == "UPSTREAM_RETRYABLE"
    assert error.value.source == "remote envelope"
    output = capsys.readouterr().err + str(error.value)
    assert "topsecret" not in output and "sensitive content" not in output
    assert "stage=export" in output


def test_unknown_or_write_tool_does_not_automatically_retry(tmp_path, monkeypatch):
    calls = scripted_proxy(tmp_path, monkeypatch, [{"ok": False, "retryable": True}])
    monkeypatch.setattr(sync, "server_url", lambda: "https://example.invalid/mcp")
    with pytest.raises(sync.SkylarkError):
        sync.call_tool("skylark_table_call", {"action": "record.create", "doc_id": 17})
    assert len(calls.read_text().splitlines()) == 1


@pytest.mark.parametrize("outcome", [{"disconnect": True}, {"partial": True}])
def test_local_transport_failures_retry_and_release_proxy(
    tmp_path, monkeypatch, outcome
):
    calls = scripted_proxy(tmp_path, monkeypatch, [outcome, {"ok": True, "data": []}])
    monkeypatch.setattr(sync, "server_url", lambda: "https://example.invalid/mcp")
    monkeypatch.setattr(sync, "timeout_s", lambda: 0.1)
    processes = []
    popen = sync.subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(sync.subprocess, "Popen", capture)
    assert sync.call_tool("skylark_doc_detail", {"doc_id": 17}) == []
    assert len(calls.read_text().splitlines()) == 2
    assert all(
        process.poll() is not None and process.stdin.closed and process.stdout.closed
        for process in processes
    )


def test_cancellation_releases_active_proxy_without_retry(tmp_path, monkeypatch):
    scripted_proxy(tmp_path, monkeypatch, [{"partial": True}])
    monkeypatch.setattr(sync, "server_url", lambda: "https://example.invalid/mcp")
    processes = []
    popen = sync.subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(sync.subprocess, "Popen", capture)
    selector = sync.selectors.DefaultSelector()

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(selector, "select", cancel)
    monkeypatch.setattr(sync.selectors, "DefaultSelector", lambda: selector)
    with pytest.raises(KeyboardInterrupt):
        sync.call_tool("skylark_doc_detail", {"doc_id": 17})
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


def test_safe_diagnostic_redacts_credentials_and_bounds_output():
    result = sync.safe_diagnostic(
        'Authorization: Bearer secret token=tok password="pass phrase" '
        "https://user:password@example.invalid/path?session=abc\n" + "x" * 500
    )
    assert all(
        secret not in result
        for secret in ("secret", "tok ", "pass phrase", "password@", "session=abc")
    )
    assert len(result) <= 300 and "\n" not in result


def test_setup_failure_releases_spawned_proxy(tmp_path, monkeypatch):
    scripted_proxy(tmp_path, monkeypatch, [])
    monkeypatch.setattr(sync, "server_url", lambda: "https://example.invalid/mcp")
    processes = []
    popen = sync.subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(sync.subprocess, "Popen", capture)

    def fail_setup(*args, **kwargs):
        raise OSError("selector unavailable")

    monkeypatch.setattr(sync.os, "set_blocking", fail_setup)
    with pytest.raises(sync.SkylarkError, match="local transport"):
        sync.call_tool("skylark_doc_detail", {"doc_id": 17})
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


def test_cancelled_threaded_listing_releases_proxy_and_stops_queued_pages(tmp_path):
    calls = tmp_path / "active.jsonl"
    release = tmp_path / "release"
    proxy = tmp_path / "blocked-proxy"
    proxy.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys,time\nfrom pathlib import Path\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'id':1,'result':{}}),flush=True)\n"
        "sys.stdin.readline()\n"
        "request=json.loads(sys.stdin.readline())\n"
        f"with Path({str(calls)!r}).open('a') as stream: stream.write(json.dumps({{'pid':os.getpid(),'offset':request['params']['arguments']['offset']}})+'\\n')\n"
        "if request['params']['arguments']['offset']==0:\n"
        " print(json.dumps({'id':2,'result':{'content':[{'text':json.dumps({'ok':True,'data':[{'id':i} for i in range(50)]})}]}}),flush=True); sys.exit(0)\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        "print(json.dumps({'id':2,'result':{'content':[{'text':json.dumps({'ok':False,'message':'cancel test finished'})}]}}),flush=True)\n"
    )
    proxy.chmod(0o755)
    program = (
        "from types import SimpleNamespace\nfrom pathlib import Path\n"
        "from scripts import get_sync_updates as sync\n"
        f"sync.proxy_bin=lambda: {str(proxy)!r}\n"
        "sync.server_url=lambda: 'https://example.invalid/mcp'\n"
        "sync.current_config=lambda: SimpleNamespace(concurrency=1)\n"
        "sync.timeout_s=lambda: 30\nsync.max_retries=lambda: 2\n"
        f"try: sync.fetch_doc_list(123,150,Path({str(tmp_path / 'pages')!r}))\n"
        "except KeyboardInterrupt: print('cancelled',flush=True)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if calls.exists() and len(calls.read_text().splitlines()) >= 2:
                break
            time.sleep(0.01)
        assert calls.exists() and len(calls.read_text().splitlines()) == 2, (
            "proxy never reached the blocking request"
        )
        child.send_signal(signal.SIGINT)
        output, errors = child.communicate(timeout=1)
        assert "cancelled" in output
        records = [json.loads(line) for line in calls.read_text().splitlines()]
        assert [row["offset"] for row in records] == [0, 50]
        assert (tmp_path / "pages" / "page-123-0000000.json").is_file()
        assert not (tmp_path / "pages" / "page-123-0000050.json").exists()
        assert "retry" not in errors
        for row in records:
            with pytest.raises(ProcessLookupError):
                os.kill(row["pid"], 0)
    finally:
        release.touch()
        try:
            child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()


def test_cancelled_listing_interrupts_retry_backoff_without_new_attempt(tmp_path):
    calls = tmp_path / "attempts"
    program = (
        "from types import SimpleNamespace\nfrom pathlib import Path\n"
        "from scripts import get_sync_updates as sync\n"
        "sync.current_config=lambda: SimpleNamespace(concurrency=1)\n"
        "sync.max_retries=lambda: 2\n"
        "sync.random.uniform=lambda low,high: 0\n"
        "def fail(tool,args):\n"
        f" with Path({str(calls)!r}).open('a') as stream: stream.write('attempt\\n')\n"
        " raise sync.SkylarkError('disconnect',retryable=True)\n"
        "sync._call_once=fail\n"
        "try: sync.fetch_doc_list(123,100)\n"
        "except KeyboardInterrupt: print('cancelled',flush=True)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([child.stderr], [], [], 5)
        assert ready, "listing never entered retry backoff"
        assert "retry 1/2" in child.stderr.readline()
        child.send_signal(signal.SIGINT)
        output, _errors = child.communicate(timeout=0.5)
        assert "cancelled" in output
        assert calls.read_text().splitlines() == ["attempt"]
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate()
