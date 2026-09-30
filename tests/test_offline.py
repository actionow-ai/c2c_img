"""Offline tests for c2c_img: no Codex calls and no quota used.

Run: python3 tests/test_offline.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skills", "c2c-img", "scripts", "c2c_img.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import c2c_img as c  # noqa: E402

sys.path.insert(0, HERE)
from fake_codex import write_png  # noqa: E402

SAMPLE = write_png(os.path.join(tempfile.mkdtemp(prefix="c2c-sample-"), "sample.png"), 836, 470)  # ~16:9


def run_cli(*args):
    p = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)
    return p.returncode, p.stdout


class Helpers(unittest.TestCase):
    def test_aspect_hint(self):
        self.assertIn("16:9", c.aspect_hint((1920, 1080)))
        self.assertIn("9:16", c.aspect_hint((1080, 1920)))
        self.assertIn("1:1", c.aspect_hint((512, 512)))
        self.assertIn("2:1", c.aspect_hint((1200, 628)))

    def test_parse_size(self):
        self.assertEqual(c.parse_size("1920x1080"), (1920, 1080))
        self.assertEqual(c.parse_size("512×512"), (512, 512))
        self.assertIsNone(c.parse_size(None))
        with self.assertRaises(ValueError):
            c.parse_size("12x")

    def test_slugify(self):
        self.assertEqual(c.slugify("Primary request: a minimal hero image of a ceramic coffee mug\n"),
                         "minimal-hero-ceramic-coffee-mug")
        self.assertEqual(c.slugify("一只猫"), "image")

    def test_unique_path_never_overwrites(self):
        d = tempfile.mkdtemp()
        try:
            open(os.path.join(d, "x.png"), "w").close()
            reserved = set()
            a = c.unique_path(os.path.join(d, "x.png"), reserved, False)
            b = c.unique_path(os.path.join(d, "x.png"), reserved, False)
            self.assertEqual([os.path.basename(a), os.path.basename(b)], ["x-v2.png", "x-v3.png"])
        finally:
            shutil.rmtree(d)

    def test_classify(self):
        base = dict(timed_out=False, errors=[], reply="", stderr_tail=[])
        cases = [
            (dict(errors=["You've hit your usage limit."]), "quota"),
            (dict(reply="FAILED: content policy"), "generation_failed"),
            (dict(errors=["The 'x' model is not supported when using Codex"]), "model"),
            (dict(errors=["401 Unauthorized"]), "auth"),
            (dict(timed_out=True), "timeout"),
            (dict(errors=["Selected model is at capacity. Please try a different model."]), "capacity"),
            (dict(error_info="usageLimitExceeded"), "quota"),
            (dict(error_info={"unauthorized": {"httpStatusCode": 401}}), "auth"),
            (dict(failure={"type": "usageLimitExceeded", "limitId": "x"}), "quota"),
            (dict(errors=["stream disconnected"]), "unknown"),
        ]
        for extra, want in cases:
            self.assertEqual(c.classify(dict(base, **extra)), want, extra)


@unittest.skipUnless(c.converter_caps()["resize"], "needs sips or Pillow")
class PostProcess(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = os.path.join(self.d, "src.png")
        shutil.copy2(SAMPLE, self.src)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_exact_size_png(self):
        out = os.path.join(self.d, "o.png")
        c.process_image(self.src, out, "png", (800, 800), 90)
        self.assertEqual(c.image_size(out), (800, 800))

    def test_jpg_with_crop(self):
        out = os.path.join(self.d, "o.jpg")
        c.process_image(self.src, out, "jpg", (1200, 628), 85)
        self.assertEqual(c.image_size(out), (1200, 628))
        with open(out, "rb") as f:
            self.assertEqual(f.read(2), b"\xff\xd8")

    def test_upscale_note(self):
        notes = c.process_image(self.src, os.path.join(self.d, "o.png"), "png", (1920, 1080), 90)
        self.assertTrue(any("upscaled" in n for n in notes))

    @unittest.skipUnless(c.converter_caps()["webp"], "needs Pillow or cwebp")
    def test_webp(self):
        out = os.path.join(self.d, "o.webp")
        c.process_image(self.src, out, "webp", (400, 400), 80)
        with open(out, "rb") as f:
            head = f.read(12)
        self.assertEqual((head[:4], head[8:12]), (b"RIFF", b"WEBP"))


class CLI(unittest.TestCase):
    def test_rejects_transparent_jpg(self):
        code, out = run_cli("-t", "-f", "jpg", "x")
        self.assertEqual(code, 2)
        self.assertIn("bad_request", out)

    def test_rejects_missing_ref(self):
        code, out = run_cli("-i", "/nonexistent.png", "x")
        self.assertEqual(code, 2)
        self.assertIn("not found", out)

    def test_doctor_runs(self):
        code, out = run_cli("--doctor")
        self.assertIn('"checks"', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
