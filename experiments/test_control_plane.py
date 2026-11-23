import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("control_plane.py")
spec = importlib.util.spec_from_file_location("cp_proto", SRC)
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

class ControlPlaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = cp.connect(str(Path(self.tmp.name) / "state.sqlite"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_duplicate_and_version_fence(self):
        self.assertEqual(cp.enqueue(self.db, "A", "v1"), "queued")
        self.assertEqual(cp.enqueue(self.db, "A", "v1"), "duplicate")
        cp.drain(self.db)
        self.assertEqual(cp.enqueue(self.db, "A", "v2"), "queued")
        self.assertEqual(self.db.execute("SELECT version,stage FROM docs").fetchone(), ("v2", 0))

    def test_independent_progress_and_graph_batch(self):
        cp.enqueue(self.db,"A","v1")
        for _ in range(3):
            cp.drain(self.db)
        cp.enqueue(self.db,"B","v1")
        result = cp.graph(self.db)
        self.assertEqual(result["compiled"], [["A","v1"]])
        self.assertEqual(self.db.execute("SELECT stage,status FROM docs WHERE doc='B'").fetchone(),(0,"ready"))

    def test_unknown_batch_blocks_dispatch(self):
        cp.enqueue(self.db,"A","v1")
        for _ in range(3): cp.drain(self.db)
        with self.db:
            self.db.execute("INSERT INTO batches(status,snapshot) VALUES('unknown','[]')")
        self.assertIn("blocked_batch",cp.graph(self.db))
        self.assertEqual(self.db.execute("SELECT status FROM docs").fetchone()[0],"ready")

if __name__ == "__main__":
    unittest.main()
