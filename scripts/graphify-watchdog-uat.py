#!/usr/bin/env python3
"""UAT watchdog (panel mode) — fixed dashboard, no scrolling.

Redraws a fixed panel in place each tick. Shows real signals:
  * overall progress + ETA (from `chunk N/M done` in the log)
  * knowledge compiled (cumulative nodes/edges from the semantic cache)
  * output size + estimated tokens (cache JSON bytes)
  * REAL per-pipeline view via py-spy: each worker thread's current chunk idx
    and its exact start time (read from the live `_run_one` frame's `idx`
    argument and `t0` local) — so current-chunk elapsed and previous-chunk
    duration are measured, not estimated.

py-spy needs ptrace permission (sudo -n is expected to be passwordless here).

Usage:
  python3 graphify-watchdog-uat.py --target ~/pdkb/graphify_flatten_ready \
      --log /tmp/full.log --conc 2 --interval 4 [--snapshot]

  --snapshot writes the current status to <snapshot-dir>/snapshot.md each tick
  (default snapshot dir: ~/pdkb/snapshot), overwriting so it is always current.

Incremental-log safety: the log is read in byte increments, pre-existing
content at startup is ignored, and a truncated/reused log (or a newly-spawned
graphify process) resets all run-scoped counters — so a stale log from a
previous run can never show a false "done" for the current run.
"""

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime

import psutil

LOG = "/tmp/graphify.log"
OUT = ""
CACHE = ""
HOME_CLEAR = "\033[2J\033[H"
MAX_CONN = 4
PYSPY = shutil.which("py-spy") or os.path.expanduser("~/.local/bin/py-spy")
TARGET = "graphify"
TARGET_NAME = "graphify"
TAG = "graphify"
SNAP_DIR = os.path.expanduser("~/pdkb/snapshot")


def log_read() -> str:
    if not os.path.exists(LOG):
        return ""
    try:
        with open(LOG, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def find_proc():
    matches = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "graphify" in cl and TARGET_NAME in cl and "extract" in cl:
            matches.append(p)
    if not matches:
        return None
    # prefer the real python/graphify CLI process over a shell wrapper so
    # py-spy and pid-tracking don't flip to a transient subshell mid-run
    for p in matches:
        if "python" in (p.info.get("name") or ""):
            return p
    return matches[0]


def spy_workers(pid):
    """Parse py-spy dump --locals for each ThreadPoolExecutor worker's idx+t0."""
    try:
        r = subprocess.run(
            ["sudo", "-n", PYSPY, "dump", "--locals", "--pid", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = r.stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    workers: dict[int, dict] = {}
    worker = None
    in_run_one = False
    for line in out.splitlines():
        m = re.match(
            r'Thread \d+ \((?:idle|active)\): "ThreadPoolExecutor-0_(\d+)"', line
        )
        if m:
            try:
                worker = int(m.group(1))
            except ValueError:
                continue
            in_run_one = False
            workers.setdefault(worker, {})
            continue
        if line.startswith("Thread "):
            worker = None
            in_run_one = False
            continue
        if worker is None:
            continue
        if "_run_one (graphify/llm.py" in line:
            in_run_one = True
            continue
        if in_run_one:
            mi = re.match(r"\s+idx: (\d+)", line)
            if mi:
                workers[worker]["idx"] = _to_int(mi.group(1))
            mt = re.match(r"\s+t0: ([\d.]+)", line)
            if mt:
                t0val = _to_float(mt.group(1))
                if t0val is not None:
                    workers[worker]["t0"] = t0val
                    in_run_one = False  # got both for this frame
    return workers


def count_src(target):
    n = 0
    if os.path.isdir(target):
        for _r, _d, files in os.walk(target):
            n += sum(1 for f in files if f.endswith(".md"))
    return n


def knowledge():
    nodes = edges = nbytes = nfiles = 0
    if not os.path.isdir(CACHE):
        return 0, 0, 0, 0
    for root, _d, files in os.walk(CACHE):
        for f in files:
            if not f.endswith(".json"):
                continue
            path = os.path.join(root, f)
            try:
                sz = os.path.getsize(path)
                with open(path) as fh:
                    d = json.load(fh)
                if isinstance(d, dict):
                    nodes += len(d.get("nodes", []))
                    edges += len(d.get("edges", []))
                nbytes += sz
                nfiles += 1
            except (OSError, ValueError):
                continue
    return nodes, edges, nbytes, nfiles


def bar(frac, width=20):
    n = max(0, min(width, round(frac * width)))
    return "#" * n + "-" * (width - n)


def fmt_dur(s):
    if s is None:
        return "-"
    return f"{s:.0f}s" if s < 90 else f"{s / 60:.1f}m"


def _to_int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _to_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def write_snapshot(lines, snap_dir, tag):
    try:
        os.makedirs(snap_dir, exist_ok=True)
    except OSError:
        return
    body = "\n".join(lines)
    md = (
        f"# graphify {tag} snapshot\n\n"
        f"- captured: {datetime.now().isoformat(timespec='seconds')}\n\n"
        f"```text\n{body}\n```\n"
    )
    try:
        with open(os.path.join(snap_dir, "snapshot.md"), "w") as fh:
            fh.write(md)
    except OSError:
        pass


def main():
    global LOG, OUT, CACHE, MAX_CONN, TARGET, TARGET_NAME, TAG, SNAP_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument(
        "--target",
        default="graphify",
        help="corpus dir path or name (proc matching by basename + default paths)",
    )
    ap.add_argument(
        "--log", default=None, help="log file (default /tmp/<basename>.log)"
    )
    ap.add_argument(
        "--out", default=None, help="output dir (default <target>/graphify-out)"
    )
    ap.add_argument("--conc", type=int, default=4, help="concurrency (worker count)")
    ap.add_argument(
        "--tag", default=None, help="panel title tag (default: target basename)"
    )
    ap.add_argument(
        "--snapshot",
        action="store_true",
        help="write current status to a markdown snapshot each tick",
    )
    ap.add_argument(
        "--snapshot-dir",
        default=SNAP_DIR,
        help=f"snapshot output dir (default {SNAP_DIR})",
    )
    ap.add_argument(
        "--total",
        type=int,
        default=0,
        help="total source files for the progress denominator (default: count .md under --target)",
    )
    args = ap.parse_args()

    TARGET = os.path.expanduser(args.target)
    TARGET_NAME = os.path.basename(os.path.abspath(TARGET).rstrip("/"))
    MAX_CONN = args.conc
    TAG = args.tag or TARGET_NAME
    SNAP_DIR = args.snapshot_dir
    LOG = args.log or f"/tmp/{TARGET_NAME}.log"
    OUT = args.out or os.path.join(TARGET, "graphify-out")
    CACHE = os.path.join(OUT, "cache", "semantic")
    TOTAL = args.total or count_src(TARGET)

    t0 = time.time()
    run_start = None
    saw_run = False
    completed = {}
    events = []
    total = 0
    trunc_total = 0
    hollow_total = 0
    invalid_total = 0
    failed_total = 0
    cur_pid = None
    offset = 0
    first = True
    absent_ticks = 0  # consecutive ticks with no graphify process
    wstate: dict[int, dict] = {
        i: {"idx": None, "t0": None, "prev": None} for i in range(MAX_CONN)
    }

    def reset_run():
        nonlocal run_start, saw_run, completed, events, total, trunc_total, hollow_total, invalid_total, failed_total, cur_pid, wstate
        run_start = None
        saw_run = False
        completed = {}
        events = []
        total = 0
        trunc_total = hollow_total = invalid_total = failed_total = 0
        cur_pid = None
        wstate = {
            i: {"idx": None, "t0": None, "prev": None} for i in range(MAX_CONN)
        }

    while True:
        now = time.time()

        # Detect the process FIRST, so a newly-started run resets state before
        # we parse any of its log lines (prevents wiping freshly-parsed progress).
        # A "new run" = a process appears after a real absence (>=3 ticks). We do
        # NOT reset on pid/create_time changes while a process stays present: pid
        # churn and transient create_time() failures during process teardown would
        # otherwise wipe the final progress (observed end-of-run reset bug).
        p = find_proc()
        if p is None:
            absent_ticks += 1
        else:
            if absent_ticks >= 3:
                reset_run()
                first = True  # re-baseline: ignore log content present at run start
            absent_ticks = 0
            cur_pid = p.pid  # track current process; pid may churn within one run

        txt = log_read()
        size = len(txt)
        if first:
            # ignore pre-existing content (could be a stale log from a prior run)
            first = False
            offset = size
            new_text = ""
        elif size < offset:
            # log truncated/reused → reset and treat the whole file as new
            reset_run()
            offset = size
            new_text = txt
        else:
            new_text = txt[offset:]
            offset = size

        done_now = re.findall(r"chunk (\d+)/(\d+) done", new_text)
        failed_now = re.findall(r"chunk (\d+)/(\d+) failed", new_text)
        trunc_total += len(re.findall(r"truncated", new_text))
        hollow_total += len(re.findall(r"hollow", new_text))
        invalid_total += len(re.findall(r"invalid JSON", new_text))
        failed_total += len(failed_now)
        if done_now:
            total = max(total, max(_to_int(x[1]) for x in done_now))
        for n, _m in done_now:
            idx = _to_int(n)
            if idx not in completed:
                completed[idx] = now
                events.append((now, f"chunk {idx}/{total} done"))
        for n, _m in failed_now:
            idx = _to_int(n)
            events.append((now, f"chunk {idx}/{total} FAILED"))

        # process / llm connections
        state = "-"
        llm = 0
        spy = {}
        if p is not None:
            saw_run = True
            if run_start is None:
                with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                    run_start = p.create_time()
            try:
                state = p.status()
                pids = {p.pid} | {c.pid for c in p.children(recursive=True)}
                for c in psutil.net_connections("tcp"):
                    if c.pid in pids and c.status == "ESTABLISHED" and c.raddr:
                        llm += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            spy = spy_workers(p.pid)

        # update per-worker state from py-spy
        for i in range(MAX_CONN):
            st = wstate[i]
            info = spy.get(i)
            new_idx = info.get("idx") if info else None
            new_t0 = info.get("t0") if info else None
            if new_idx is None:
                if st["idx"] is not None and st["t0"] is not None:
                    st["prev"] = now - st["t0"]
                st["idx"] = None
                st["t0"] = None
            elif st["idx"] != new_idx:
                if (
                    st["idx"] is not None
                    and st["t0"] is not None
                    and new_t0 is not None
                ):
                    st["prev"] = new_t0 - st["t0"]
                st["idx"] = new_idx
                st["t0"] = new_t0

        nodes, edges, nbytes, nfiles = knowledge()
        est_out_tok = nbytes // 4
        done = len(completed)
        elapsed = now - (run_start or t0)
        frac = done / total if total else 0.0
        rate = done / (elapsed / 60) if elapsed > 0 else 0.0
        eta = ((total - done) / rate) * 60 if rate > 0 else None

        src_str = f"src-files {nfiles}/{TOTAL}" if TOTAL else f"src-files {nfiles}"
        lines = [
            f"=== graphify {TAG} Monitor ===",
            f"model deepseek-v4-flash | think ON | out 384000 | conc {MAX_CONN}",
            "-- Overall " + "-" * 45,
            f"chunk {done:>3}/{total:<3} done  [{bar(frac)}] {frac * 100:>3.0f}%",
            f"rate {rate:.2f}/min   ETA {fmt_dur(eta)}   elapsed {fmt_dur(elapsed)}   state {state}",
            f"quality: trunc {trunc_total}  hollow {hollow_total}  invalid {invalid_total}  failed {failed_total}",
            "-- Pipelines (py-spy, real) " + "-" * 28,
        ]
        for i in range(MAX_CONN):
            st = wstate[i]
            if st["idx"] is not None:
                cur = st["idx"] + 1
                running = now - (st["t0"] or now)
                lines.append(
                    f"W{i + 1} busy chunk #{cur:<3} running {fmt_dur(running):>6}  prev {fmt_dur(st['prev']):>6}"
                )
            else:
                lines.append(
                    f"W{i + 1} idle {'-':<12} running {'-':>6}  prev {fmt_dur(st['prev']):>6}"
                )
        lines += [
            "-- Knowledge Compiled " + "-" * 33,
            f"nodes {nodes}   edges {edges}   {src_str}",
            f"output {nbytes / 1e6:.2f} MB JSON   (~{est_out_tok:,} tokens est)   llm {llm} live",
            "-- Recent Chunk Completions " + "-" * 27,
        ]
        for ts, ev in events[-4:]:
            lines.append(f"@{datetime.fromtimestamp(ts).strftime('%H:%M:%S')}  {ev}")
        if saw_run and p is None and elapsed > 60:
            lines.append("*** RUN DONE (no graphify process) ***")

        try:
            term_w = shutil.get_terminal_size((113, 28)).columns
        except OSError:
            term_w = 113
        lines = [ln[: max(1, term_w - 1)] for ln in lines]

        if args.snapshot:
            write_snapshot(lines, SNAP_DIR, TAG)

        print(HOME_CLEAR + "\n".join(lines), end="", flush=True)
        if saw_run and p is None and elapsed > 60:
            print(flush=True)
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
