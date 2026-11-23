"""Isolated control-plane experiment. No production tool or network calls.

Commands:
  python3 experiments/control_plane.py init --db /tmp/cp.db
  python3 experiments/control_plane.py enqueue --db /tmp/cp.db --doc A --version v1
  python3 experiments/control_plane.py drain --db /tmp/cp.db
  python3 experiments/control_plane.py status --db /tmp/cp.db
  python3 experiments/control_plane.py graph --db /tmp/cp.db
"""
import argparse
import json
import sqlite3
from pathlib import Path

STAGES = ("download", "compare", "ocr")
SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
 doc TEXT PRIMARY KEY, version TEXT NOT NULL,
 stage INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ready',
 error TEXT
);
CREATE TABLE IF NOT EXISTS batches (
 id INTEGER PRIMARY KEY, status TEXT NOT NULL, snapshot TEXT NOT NULL
);
"""

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db

def enqueue(db, doc, version):
    with db:
        old = db.execute("SELECT version FROM docs WHERE doc=?", (doc,)).fetchone()
        if old and old[0] == version:
            return "duplicate"
        db.execute("INSERT INTO docs(doc,version,stage,status,error) VALUES(?,?,0,'ready',NULL) "
                   "ON CONFLICT(doc) DO UPDATE SET version=excluded.version,stage=0,status='ready',error=NULL",
                   (doc, version))
    return "queued"

def drain(db):
    # One item advances one stage per transaction, avoiding a global stage barrier.
    advanced = []
    for doc, version, stage in db.execute(
        "SELECT doc,version,stage FROM docs WHERE status='ready' AND stage<3 ORDER BY doc"
    ).fetchall():
        with db:
            cur = db.execute("UPDATE docs SET stage=stage+1 WHERE doc=? AND version=? AND stage=? "
                             "AND status='ready'", (doc, version, stage))
        if cur.rowcount:
            advanced.append([doc, STAGES[stage]])
    return advanced

def graph(db):
    # In production, replace success simulation with a Graphify native CLI adapter.
    # Never automatically retry an unknown/failed batch.
    active = db.execute("SELECT id FROM batches WHERE status IN ('running','unknown','failed')").fetchone()
    if active:
        return {"blocked_batch": active[0]}
    rows = db.execute("SELECT doc,version FROM docs WHERE status='ready' AND stage=3 ORDER BY doc").fetchall()
    if not rows:
        return {"empty": True}
    snapshot = json.dumps(rows)
    with db:
        cur = db.execute("INSERT INTO batches(status,snapshot) VALUES('running',?)", (snapshot,))
        batch_id = cur.lastrowid
    # Simulated successful graph commit; crash between these commits leaves 'running'.
    # Recovery must reconcile the actual external output before authorizing any retry.
    with db:
        db.execute("UPDATE batches SET status='succeeded' WHERE id=?", (batch_id,))
        for doc, version in rows:
            db.execute("UPDATE docs SET status='compiled' WHERE doc=? AND version=? AND stage=3",
                       (doc, version))
    return {"batch": batch_id, "compiled": rows}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init","enqueue","drain","graph","status"))
    parser.add_argument("--db", default="/tmp/dogpdkb-cp-python.sqlite")
    parser.add_argument("--doc")
    parser.add_argument("--version")
    args = parser.parse_args()
    with connect(args.db) as db:
        if args.command == "init":
            output = {"initialized": args.db}
        elif args.command == "enqueue":
            if not args.doc or not args.version:
                parser.error("enqueue requires --doc and --version")
            output = {"result": enqueue(db,args.doc,args.version)}
        elif args.command == "drain":
            output = {"advanced": drain(db)}
        elif args.command == "graph":
            output = graph(db)
        else:
            output = {"docs": db.execute("SELECT doc,version,stage,status FROM docs ORDER BY doc").fetchall(),
                      "batches": db.execute("SELECT id,status,snapshot FROM batches ORDER BY id").fetchall()}
        print(json.dumps(output,ensure_ascii=False))

if __name__ == "__main__":
    main()
