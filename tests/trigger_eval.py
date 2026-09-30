"""Trigger eval: does Claude reach for c2c-img when it should, and only then?

Runs `claude -p` on a set of should-trigger and should-not-trigger prompts inside a temp
folder holding a few sample photos. Only read-only tools and the Skill tool are allowed, so
no image is generated, no Codex quota is used, and nothing is written. The script records
the first thing Claude does:
  skill  = invoked c2c-img (Skill tool, or ran c2c_img.py)
  code   = tried to process the image with code/filters (PIL, OpenCV, ImageMagick, sips, ...)
  other  = neither

Needs a logged-in Claude Code CLI (`claude auth login`) with the c2c-img plugin installed,
or pass --plugin-dir to test a working copy. It uses your Claude usage (about 1-2k tokens
per run).

Run: python3 tests/trigger_eval.py [-r RUNS] [--plugin-dir PATH] [--only substring]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = ["edit-before.jpg", "reading-nook.jpg", "poster-zh.jpg"]

CASES = [
    # (prompt, should_trigger)
    ("随机选择一张照片，并将选中的照片卡通化（像素画风格）", True),
    ("把 IMG_001.jpg 变成吉卜力风格", True),
    ("Make IMG_002.jpg look like a watercolor painting", True),
    ("把 IMG_001.jpg 的背景换成海边日落", True),
    ("画一只戴墨镜的柯基，赛博朋克风", True),
    ("给我的博客做一张 1200x630 的封面图，主题是远程办公", True),
    ("把 IMG_003.jpg 做成 Q 版头像", True),
    ("把 IMG_001.jpg 压缩到 200KB 以内", False),
    ("把 IMG_002.jpg 裁成正方形", False),
    ("把 IMG_001.jpg 转成 png 格式", False),
    ("画一个用户注册流程的流程图", False),
]

CODE_RE = re.compile(r"\bPIL\b|ImageFilter|ImageOps|cv2|opencv|\bmagick\b|\bconvert\b|\bsips\b|quantize|pixelat",
                     re.I)


def classify(events):
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for c in ev["message"].get("content", []):
            if c.get("type") != "tool_use":
                continue
            name, inp = c.get("name"), c.get("input") or {}
            if name == "Skill" and "c2c-img" in str(inp.get("skill", "")):
                return "skill"
            text = json.dumps(inp, ensure_ascii=False)
            if "c2c_img.py" in text:
                return "skill"
            if name in ("Bash", "Write") and CODE_RE.search(text):
                return "code"
    return "other"


def run_case(prompt, workdir, plugin_dir, timeout):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", "6",
           "--allowedTools", "Bash(ls:*)", "Bash(find:*)", "Read", "Glob", "Grep", "Skill"]
    if plugin_dir:
        cmd += ["--plugin-dir", plugin_dir]
    try:
        p = subprocess.run(cmd, cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    events = []
    for line in p.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    final = next((e.get("result", "") for e in events if e.get("type") == "result"), "")
    if "Not logged in" in final:
        sys.exit("claude CLI is not logged in. Run `claude auth login` first.")
    return classify(events), final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-r", "--runs", type=int, default=1, help="runs per prompt")
    ap.add_argument("--plugin-dir", help="load this plugin checkout instead of the installed one")
    ap.add_argument("--only", help="run only prompts containing this substring")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    work = tempfile.mkdtemp(prefix="c2c-trigger-")
    for i, name in enumerate(SAMPLES, 1):
        shutil.copy(os.path.join(HERE, "..", "docs", "images", name), os.path.join(work, "IMG_%03d.jpg" % i))

    cases = [c for c in CASES if not args.only or args.only in c[0]]
    passed = total = 0
    try:
        for prompt, want in cases:
            outcomes = []
            for _ in range(args.runs):
                got, _ = run_case(prompt, work, args.plugin_dir, args.timeout)
                outcomes.append(got)
                ok = (got == "skill") == want
                passed += ok
                total += 1
            mark = "✓" if all((o == "skill") == want for o in outcomes) else "✗"
            print("%s %-7s %-44s -> %s" % (mark, "trigger" if want else "skip", prompt[:44], ", ".join(outcomes)),
                  flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("\n%d/%d runs as expected" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
