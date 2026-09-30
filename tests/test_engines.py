"""End-to-end tests of both engines (daemon and exec) against tests/fake_codex.py.

Needs no Codex login and uses no quota. Every test gets its own CODEX_HOME and daemon
state dir, so it never touches a real installation.
Run: python3 tests/test_engines.py
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skills", "c2c_img", "scripts", "c2c_img.py")
FAKE = os.path.join(HERE, "fake_codex.py")
IS_WIN = os.name == "nt"


def make_shim(dirpath):
    """An executable named like codex that runs fake_codex.py with this Python."""
    if IS_WIN:
        path = os.path.join(dirpath, "codex.cmd")
        with open(path, "w") as f:
            f.write('@"%s" "%s" %%*\r\n' % (sys.executable, FAKE))
    else:
        path = os.path.join(dirpath, "codex")
        with open(path, "w") as f:
            f.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FAKE))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Engines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="c2c-test-")
        self.home = os.path.join(self.tmp, "codex_home")
        self.out = os.path.join(self.tmp, "out")
        cache = os.path.join(self.tmp, "cache")
        os.makedirs(self.home)
        self.env = dict(os.environ, CODEX_HOME=self.home, XDG_CACHE_HOME=cache, LOCALAPPDATA=cache,
                        C2C_CODEX_BIN=make_shim(self.tmp), C2C_DAEMON_IDLE="120", PYTHONIOENCODING="utf-8")
        for k in ("C2C_DAEMON", "C2C_USE_USER_CONFIG", "C2C_MODEL", "C2C_OUT_DIR"):
            self.env.pop(k, None)

    def tearDown(self):
        self.cli("--stop-daemon")
        time.sleep(2.5)  # the daemon's watchdog polls every 2 s
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, mode="ok", stdin=None):
        p = subprocess.run([sys.executable, SCRIPT, *args], env=dict(self.env, FAKE_CODEX_MODE=mode),
                           input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=180)
        try:
            return json.loads(p.stdout)
        except ValueError:
            self.fail("non-JSON output (exit %s):\nstdout=%s\nstderr=%s" % (p.returncode, p.stdout, p.stderr))

    def gen_leftovers(self):
        d = os.path.join(self.home, "generated_images")
        return [os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs] if os.path.isdir(d) else []

    # --- exec engine ---

    def test_exec_ok_stops_early_and_moves_file(self):
        r = self.cli("--no-daemon", "-o", os.path.join(self.out, "a.png"), "画一只猫 · unicode ✓")
        self.assertTrue(r["ok"], r)
        res = r["results"][0]
        self.assertEqual(res["engine"], "exec-lean")
        self.assertTrue(os.path.isfile(res["path"]))
        self.assertLess(res["elapsed_s"], 5, "should stop codex as soon as the image is complete")
        self.assertEqual(self.gen_leftovers(), [], "original should be moved, not copied")

    def test_exec_quota_is_not_retried(self):
        r = self.cli("--no-daemon", "x", mode="quota")
        res = r["results"][0]
        self.assertEqual(res["error_type"], "quota")
        self.assertNotIn("notes", res)

    # --- daemon engine ---

    def test_daemon_ok_and_reused(self):
        r1 = self.cli("-o", os.path.join(self.out, "b.png"), "x")
        self.assertTrue(r1["ok"], r1)
        self.assertEqual(r1["results"][0]["engine"], "daemon")
        doc = self.cli("--doctor")
        daemon = next(c for c in doc["checks"] if c["check"] == "daemon")
        self.assertIn("running", daemon["detail"])
        pid = daemon["detail"]
        r2 = self.cli("-o", os.path.join(self.out, "c.png"), "y")
        self.assertEqual(r2["results"][0]["engine"], "daemon")
        doc2 = self.cli("--doctor")
        self.assertEqual(next(c for c in doc2["checks"] if c["check"] == "daemon")["detail"].split(",")[0],
                         pid.split(",")[0], "second call should reuse the same daemon")
        self.assertEqual(self.gen_leftovers(), [])

    def test_daemon_batch_concurrent(self):
        jobs = "\n".join(json.dumps({"prompt": "job %d 中文" % i, "out": os.path.join(self.out, "j%d.png" % i)})
                         for i in range(3))
        r = self.cli("--batch", "-", stdin=jobs)
        self.assertTrue(r["ok"], r)
        self.assertEqual([x["engine"] for x in r["results"]], ["daemon"] * 3)
        self.assertEqual(len(set(r["images"])), 3)

    def test_daemon_quota_reports_reset_time(self):
        r = self.cli("x", mode="quota")
        res = r["results"][0]
        self.assertEqual(res["error_type"], "quota")
        self.assertIn("resets_at", res)
        self.assertEqual(res["engine"], "daemon", "quota must not fall back to exec")

    def test_daemon_capacity_retries_other_model(self):
        r = self.cli("-o", os.path.join(self.out, "cap.png"), "x", mode="capacity")
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("at capacity" in n for n in r["results"][0].get("notes", [])))

    def test_refusal_is_reported_not_retried(self):
        r = self.cli("x", mode="refuse")
        res = r["results"][0]
        self.assertEqual(res["error_type"], "generation_failed")
        self.assertEqual(res["engine"], "daemon")

    def test_daemon_start_failure_falls_back_fast(self):
        t = time.time()
        r = self.cli("-o", os.path.join(self.out, "fb.png"), "x", mode="no_app_server")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["results"][0]["engine"], "exec-lean")
        self.assertLess(time.time() - t, 15, "should not wait the full daemon start timeout")

    def test_daemon_replaced_after_codex_upgrade(self):
        self.assertTrue(self.cli("x")["ok"])
        pid1 = next(c for c in self.cli("--doctor")["checks"] if c["check"] == "daemon")["detail"]
        shim = self.env["C2C_CODEX_BIN"]
        with open(shim, "a") as f:  # a changed codex binary means a new fingerprint
            f.write("\n")
        r = self.cli("x")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["results"][0]["engine"], "daemon")
        pid2 = next(c for c in self.cli("--doctor")["checks"] if c["check"] == "daemon")["detail"]
        self.assertNotEqual(pid1.split(",")[0], pid2.split(",")[0])

    def test_unique_names_across_calls(self):
        path = os.path.join(self.out, "same.png")
        a = self.cli("-o", path, "x")["images"][0]
        b = self.cli("-o", path, "x")["images"][0]
        self.assertEqual(os.path.basename(a), "same.png")
        self.assertEqual(os.path.basename(b), "same-v2.png")


if __name__ == "__main__":
    unittest.main(verbosity=2)
