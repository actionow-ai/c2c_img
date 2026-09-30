#!/usr/bin/env python3
"""c2c_img: generate or edit images with Codex's built-in image_gen tool.

Asks Codex to call image_gen exactly once with the prompt verbatim, then takes the
real file Codex saved and moves it to the output path, resizing or converting it
if asked. Prints one JSON object on stdout; progress goes to stderr.

There are two engines:
  daemon (default)  A small background process keeps `codex app-server` warm
                    between calls, which saves about 7 s per image. It exits on
                    its own after 15 idle minutes.
  exec (fallback)   Runs `codex exec` once per image. It is used automatically
                    whenever the daemon can't serve a request.

Uses the user's ChatGPT login in Codex; no OPENAI_API_KEY is needed.
Stdlib only, Python 3.9+, macOS / Linux / Windows.
"""

import argparse
import concurrent.futures
import datetime
import glob
import hmac
import json
import math
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time

VERSION = "0.4.1"
IS_WIN = os.name == "nt"
TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}
APP_BUNDLED_CLIS = [
    # CLI shipped inside the ChatGPT desktop app (Codex tab); tracks the app's models.
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
]
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
FORMATS = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "webp": ".webp"}
DEFAULT_OUT_DIR = os.environ.get("C2C_OUT_DIR") or os.path.join(os.path.expanduser("~"), "Pictures", "c2c_img")
DAEMON_IDLE_S = int(os.environ.get("C2C_DAEMON_IDLE", "900"))
# Tools a headless image run never needs. Dropping them trims about 17% of Codex's prompt
# and prevents Codex from running shell commands. image_gen is reached through
# code_mode_host, which stays enabled.
LEAN_DISABLE = [
    "browser_use", "browser_use_external", "browser_use_full_cdp_access", "in_app_browser",
    "computer_use", "apps", "plugins", "remote_plugin", "plugin_sharing", "multi_agent",
    "goals", "hooks", "tool_suggest", "skill_search", "skill_mcp_dependency_install",
    "sleep_tool", "shell_tool",
]
COMMON_RATIOS = [(1, 1), (5, 4), (4, 5), (4, 3), (3, 4), (3, 2), (2, 3), (16, 9), (9, 16),
                 (2, 1), (1, 2), (21, 9), (9, 21), (3, 1), (1, 3)]
HINTS = {
    "quota": "The ChatGPT/Codex usage limit has been reached. Tell the user (include resets_at if present) "
             "and don't retry until it resets.",
    "auth": "Codex is not logged in. The user should run `codex login` with a ChatGPT account.",
    "model": "The codex CLI rejected the model. Pass --model <slug> or set C2C_CODEX_BIN to a newer CLI.",
    "timeout": "Retry once. If it times out again, Codex/OpenAI may be degraded; tell the user.",
    "capacity": "OpenAI is at capacity right now (another model was already tried). Retry in a minute.",
    "generation_failed": "Read Codex's reason. If it's a content-policy refusal, rephrase or explain why "
                         "to the user (never try to evade the policy). Otherwise retry once.",
    "unknown": "Run `c2c_img.py --doctor` to check the setup.",
}


# ---------- platform helpers ----------

def group_kwargs(detached=False):
    """Popen kwargs that put the child in its own process group so the whole tree
    can be killed, and that open no console window on Windows."""
    if IS_WIN:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= subprocess.DETACHED_PROCESS if detached else 0x08000000  # CREATE_NO_WINDOW
        return {"creationflags": flags}
    return {"start_new_session": True}


def kill_tree(proc, grace=5):
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            return
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def try_lock(path):
    """Take a non-blocking exclusive lock that lasts for the process's lifetime.
    Returns the fd, or None if another process already holds the lock."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if IS_WIN:
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def state_dir():
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    d = os.path.join(base, "c2c_img")
    os.makedirs(d, exist_ok=True)
    return d


# ---------- codex discovery ----------

def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def cli_version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, stdin=subprocess.DEVNULL,
                             timeout=15, **TEXT).stdout
    except Exception:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def resolve_codex(explicit):
    """Choose the codex binary. The desktop app's bundled CLI is usually newer than a
    Homebrew or npm install, and it knows the models set in ~/.codex/config.toml."""
    if explicit:
        return explicit
    candidates = [p for p in APP_BUNDLED_CLIS if os.access(p, os.X_OK)]
    on_path = shutil.which("codex")
    if on_path:
        candidates.append(on_path)
    best, best_ver = (candidates[0] if candidates else None), None
    for c in candidates:
        v = cli_version(c)
        if v and (best_ver is None or v > best_ver):
            best, best_ver = c, v
    return best


def file_fingerprint(path):
    try:
        st = os.stat(path)
        return "%s:%d:%d" % (os.path.realpath(path), st.st_size, int(st.st_mtime))
    except OSError:
        return path


def daemon_fingerprint(codex):
    """Identifies what a daemon runs. If the codex binary or this script changes (for example,
    after an upgrade), the old daemon is replaced."""
    return file_fingerprint(codex) + "|" + file_fingerprint(os.path.abspath(__file__))


def user_mcp_servers():
    """Names of the MCP servers in the user's config.toml, found with a regex because Python 3.9 has no TOML parser."""
    try:
        with open(os.path.join(codex_home(), "config.toml"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    return sorted({m[1] for m in re.findall(r'^\s*\[mcp_servers\.("?)([A-Za-z0-9_-]+)\1\s*\]', text, re.M)})


# ---------- image helpers ----------

def png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return None


def have_pil():
    try:
        import PIL.Image  # noqa: F401
        return True
    except ImportError:
        return False


def image_size(path):
    size = png_size(path)
    if size:
        return size
    if have_pil():
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    if shutil.which("sips"):
        out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                             capture_output=True, **TEXT).stdout
        w, h = re.search(r"pixelWidth: (\d+)", out), re.search(r"pixelHeight: (\d+)", out)
        if w and h:
            return int(w.group(1)), int(h.group(1))
    return None


def image_complete(path):
    """Return True once a generated file is fully written. A PNG must end with its IEND chunk."""
    try:
        if os.path.getsize(path) < 1024:
            return False
        if path.lower().endswith(".png"):
            with open(path, "rb") as f:
                f.seek(-12, os.SEEK_END)
                return f.read(12)[4:8] == b"IEND"
    except OSError:
        return False
    return True


def wait_complete(path, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if image_complete(path):
            return True
        time.sleep(0.2)
    return False


def converter_caps():
    pil, sips, cwebp = have_pil(), bool(shutil.which("sips")), bool(shutil.which("cwebp"))
    return {"resize": pil or sips, "jpg": pil or sips, "webp": pil or cwebp,
            "backends": [n for n, ok in (("pillow", pil), ("sips", sips), ("cwebp", cwebp)) if ok]}


def aspect_hint(size):
    w, h = size
    r = math.log(w / h)
    rw, rh = min(COMMON_RATIOS, key=lambda p: abs(math.log(p[0] / p[1]) - r))
    shape = "square" if rw == rh else ("wide landscape" if rw > rh else "tall portrait")
    return "Aspect ratio: %d:%d %s." % (rw, rh, shape)


def run_quiet(cmd):
    p = subprocess.run(cmd, capture_output=True, **TEXT)
    if p.returncode != 0:
        raise RuntimeError("%s failed: %s" % (cmd[0], (p.stderr or p.stdout).strip()[:300]))


def process_image(src, dst, fmt, size, quality):
    """Write src to dst as fmt. When a size is given, scale to cover it and center-crop.
    Returns a list of notes."""
    notes = []
    w, h = image_size(src) or (0, 0)
    if size and (size[0] > w or size[1] > h):
        notes.append("upscaled from %dx%d; expect some softness" % (w, h))
    if have_pil():
        from PIL import Image, ImageOps
        lanczos = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(src) as im:
            im.load()
            if size:
                im = ImageOps.fit(im, size, method=lanczos, centering=(0.5, 0.5))
            if fmt == "jpg":
                if im.mode in ("RGBA", "LA", "P"):
                    rgba = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.split()[-1])
                    im = bg
                im.convert("RGB").save(dst, "JPEG", quality=quality, optimize=True)
            elif fmt == "webp":
                im.save(dst, "WEBP", quality=quality, method=6)
            else:
                im.save(dst, "PNG", optimize=True)
        return notes
    # No Pillow: use macOS sips for resizing and PNG/JPEG, and cwebp for WebP.
    tmp = os.path.join(os.path.dirname(dst), ".c2c-%d-%s.png" % (os.getpid(), os.path.basename(dst)))
    shutil.copy2(src, tmp)
    try:
        if size:
            scale = max(size[0] / w, size[1] / h)
            run_quiet(["sips", "--resampleHeightWidth", str(math.ceil(h * scale)),
                       str(math.ceil(w * scale)), tmp])
            run_quiet(["sips", "--cropToHeightWidth", str(size[1]), str(size[0]), tmp])
        if fmt == "jpg":
            run_quiet(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality),
                       tmp, "--out", dst])
        elif fmt == "webp":
            run_quiet(["cwebp", "-quiet", "-q", str(quality), "-alpha_q", "100", tmp, "-o", dst])
        else:
            os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return notes


# ---------- output paths ----------

STOP_WORDS = {"a", "an", "the", "of", "and", "with", "in", "on", "at", "for", "to", "use", "case",
              "asset", "type", "primary", "request", "edit", "image", "only", "no", "text"}


def slugify(prompt):
    m = re.search(r"Primary request:\s*(.+)", prompt)
    words = [w for w in re.findall(r"[a-z0-9]+", (m.group(1) if m else prompt).lower())
             if w not in STOP_WORDS]
    return "-".join(words[:6])[:48].strip("-") or "image"


def unique_path(path, reserved, overwrite):
    if overwrite:
        reserved.add(path)
        return path
    stem, ext = os.path.splitext(path)
    m = re.match(r"(.*)-v(\d+)$", stem)
    base, k = (m.group(1), int(m.group(2))) if m else (stem, 1)
    cand = path
    while cand in reserved or os.path.exists(cand):
        k += 1
        cand = "%s-v%d%s" % (base, k, ext)
    reserved.add(cand)
    return cand


# ---------- job planning ----------

def parse_size(s):
    if not s:
        return None
    m = re.fullmatch(r"\s*(\d{2,5})\s*[xX*×]\s*(\d{2,5})\s*", str(s))
    if not m:
        raise ValueError("size must look like 1920x1080, got %r" % s)
    return int(m.group(1)), int(m.group(2))


def plan_jobs(args):
    """Turn the CLI args or a batch file into fully resolved job dicts."""
    if args.batch:
        if args.batch == "-":
            text = sys.stdin.read()
        else:
            with open(args.batch, encoding="utf-8") as f:
                text = f.read()
        text = text.strip()
        raw = json.loads(text) if text.startswith("[") else \
            [json.loads(line) for line in text.splitlines() if line.strip()]
        raw = [{"prompt": r} if isinstance(r, str) else r for r in raw]
    else:
        raw = [{"prompt": args.prompt, "out": args.out, "ref": args.ref} for _ in range(args.n)]

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    reserved, jobs = set(), []
    for i, r in enumerate(raw):
        prompt = (r.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("job %d has an empty prompt" % (i + 1))
        refs = r.get("ref") or []
        refs = [refs] if isinstance(refs, str) else list(refs)
        for p in refs:
            if not os.path.isfile(p):
                raise ValueError("input image not found: %s" % p)
        transparent = bool(r.get("transparent", args.transparent))
        size = parse_size(r.get("size", args.size))
        out = r.get("out") if args.batch else (r.get("out") or args.out)
        fmt = (r.get("format") or args.format or
               (os.path.splitext(out)[1].lstrip(".").lower() if out else "") or "png")
        if fmt not in FORMATS:
            raise ValueError("unsupported format %r (use png, jpg or webp)" % fmt)
        fmt = "jpg" if fmt == "jpeg" else fmt
        if transparent and fmt == "jpg":
            raise ValueError("JPEG has no transparency; use png or webp with a transparent background")

        if out:
            out = os.path.abspath(os.path.expanduser(out))
            if os.path.splitext(out)[1].lower() not in IMAGE_EXTS:
                out += FORMATS[fmt]
            elif os.path.splitext(out)[1].lower() != FORMATS[fmt] and not \
                    (fmt == "jpg" and out.lower().endswith(".jpeg")):
                out = os.path.splitext(out)[0] + FORMATS[fmt]
        else:
            out = os.path.join(os.path.abspath(os.path.expanduser(args.out_dir)),
                               "%s-%s%s" % (stamp, slugify(prompt), FORMATS[fmt]))
        if not args.batch and args.n > 1:
            stem, ext = os.path.splitext(out)
            out = "%s-%d%s" % (stem, i + 1, ext)

        image_prompt = prompt + ("\n\n" + aspect_hint(size) if size else "")
        jobs.append({"index": i, "prompt": image_prompt, "refs": [os.path.abspath(p) for p in refs],
                     "transparent": transparent, "size": size, "fmt": fmt,
                     "out": unique_path(out, reserved, args.overwrite)})
    return jobs


def build_driver_prompt(job):
    lines = [
        "You are a headless image-generation backend called by another AI agent.",
        "Call your built-in image_gen tool EXACTLY ONCE with:",
        "- prompt: the text inside <image_prompt> below, passed VERBATIM "
        "(do not rewrite, expand, shorten, or translate it)",
        "- transparent_background: %s" % ("true" if job["transparent"] else "false"),
    ]
    if job["refs"]:
        lines.append("The %d attached image(s) are inputs for this generation, in order "
                     "(Image 1 = first attachment). Use them as the prompt describes."
                     % len(job["refs"]))
    lines += [
        "Do NOT read skills or docs, run commands, create or modify files, or inspect the result.",
        "As soon as the tool returns, reply with exactly `DONE`, or `FAILED: <reason>` "
        "if the tool errored or refused.",
        "",
        "<image_prompt>",
        job["prompt"],
        "</image_prompt>",
    ]
    return "\n".join(lines)


# ---------- error classification ----------

QUOTA_RE = re.compile(r"usage.?limit|rate.?limit|quota|too many requests|\b429\b", re.I)
CAPACITY_RE = re.compile(r"at capacity|overloaded|try a different model", re.I)
MODEL_RE = re.compile(r"model.{0,40}(not supported|not found|unknown|does not exist)", re.I)
AUTH_RE = re.compile(r"\b401\b|unauthori[sz]ed|not logged in|log ?in|authenticat", re.I)
# codexErrorInfo codes reported by app-server
ERROR_INFO_KIND = {"usageLimitExceeded": "quota", "rateLimitExceeded": "quota",
                   "sessionBudgetExceeded": "quota", "unauthorized": "auth", "badRequest": "model",
                   "serverOverloaded": "capacity",
                   "cyberPolicy": "generation_failed", "misalignmentPolicyViolation": "generation_failed"}


def info_code(info):
    if isinstance(info, str):
        return info
    if isinstance(info, dict) and info:
        return next(iter(info))
    return None


def classify(r):
    if r["timed_out"]:
        return "timeout"
    failure = r.get("failure") or {}
    if failure.get("type") == "usageLimitExceeded":
        return "quota"
    code = info_code(r.get("error_info"))
    if code in ERROR_INFO_KIND:
        return ERROR_INFO_KIND[code]
    text = " ".join([e for e in r["errors"] if e] + [r["reply"]] + r.get("stderr_tail", []))
    if QUOTA_RE.search(text):
        return "quota"
    if CAPACITY_RE.search(text):
        return "capacity"
    if r["reply"].startswith("FAILED"):
        return "generation_failed"
    if MODEL_RE.search(text):
        return "model"
    if AUTH_RE.search(text):
        return "auth"
    return "unknown"


# ---------- engine 1: codex exec ----------

def thread_images(thread_id):
    d = os.path.join(codex_home(), "generated_images", thread_id)
    imgs = [p for p in glob.glob(os.path.join(d, "*")) if p.lower().endswith(IMAGE_EXTS)]
    return sorted(imgs, key=os.path.getmtime)


def run_exec(job, opts, codex, lean):
    """Run one headless `codex exec` turn. It returns as soon as the image file is complete
    and stops Codex early: otherwise Codex would spend 10-25 s re-reading the image just to
    say DONE. It also returns when Codex exits or the timeout passes."""
    t0 = time.time()
    workdir = tempfile.mkdtemp(prefix="c2c_img-")  # keeps project AGENTS.md/git out of Codex's context
    cmd = [codex, "exec", "--json", "--skip-git-repo-check", "-s", "read-only", "-C", workdir,
           "--enable", "image_generation"]
    if lean:
        # Skip the user's plugins, MCP servers and model pins. Startup is faster, and the
        # CLI's own default model is always one that this CLI version supports.
        cmd += ["--ignore-user-config", "--ignore-rules", "-c", 'model_reasoning_effort="low"']
        for f in LEAN_DISABLE:
            cmd += ["--disable", f]
    if not opts.keep_session:
        cmd.append("--ephemeral")
    if opts.model:
        cmd += ["-m", opts.model]
    for r in job["refs"]:
        cmd.append("--image=" + r)
    # Send the prompt on stdin and close it. This avoids quoting problems, and it avoids a
    # hang where codex waits forever on an inherited stdin pipe that never closes.
    cmd += ["--", "-"]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **TEXT, **group_kwargs())
    try:
        proc.stdin.write(build_driver_prompt(job))
        proc.stdin.close()
    except (BrokenPipeError, OSError):  # codex exited during startup; its errors are read below
        pass
    out_lines, err_lines = [], []

    def pump(stream, sink):
        for line in stream:
            sink.append(line)

    pumps = [threading.Thread(target=pump, args=a, daemon=True)
             for a in ((proc.stdout, out_lines), (proc.stderr, err_lines))]
    for t in pumps:
        t.start()

    thread_id, src, sizes, timed_out = None, None, {}, False
    while True:
        if thread_id is None:
            for line in list(out_lines):
                if '"thread.started"' in line:
                    thread_id = json.loads(line).get("thread_id")
        if thread_id:
            for pth in thread_images(thread_id):
                size = os.path.getsize(pth)
                if sizes.get(pth) == size and image_complete(pth):
                    src = pth
                sizes[pth] = size
        if src:
            kill_tree(proc)
            break
        if proc.poll() is not None:
            if thread_id:  # the image may have landed between polls
                done = [p for p in thread_images(thread_id) if image_complete(p)]
                src = done[-1] if done else None
            break
        if time.time() - t0 > opts.timeout:
            timed_out = True
            kill_tree(proc)
            break
        time.sleep(0.4)
    for t in pumps:
        t.join(timeout=5)
    shutil.rmtree(workdir, ignore_errors=True)

    reply, errors = [], []
    for line in out_lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        et = ev.get("type")
        if et == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
            reply.append(ev["item"].get("text", ""))
        elif et in ("error", "turn.failed"):
            msg = ev.get("message") or (ev.get("error") or {}).get("message")
            if msg and msg not in errors:
                errors.append(msg)
    return {"engine": "exec-lean" if lean else "exec-config", "src": src, "timed_out": timed_out,
            "errors": errors, "reply": " ".join(reply).strip(),
            "stderr_tail": [l.rstrip() for l in err_lines[-5:]],
            "elapsed_s": round(time.time() - t0, 1)}


# ---------- engine 2: warm app-server behind a small daemon ----------

class RpcError(Exception):
    pass


class AppServer:
    """A minimal JSON-RPC client for `codex app-server` over stdio."""

    def __init__(self, codex, log):
        cmd = [codex, "app-server", "--enable", "image_generation", "-c", "notify=[]"]
        for f in LEAN_DISABLE:
            cmd += ["--disable", f]
        # app-server can't ignore the user config, so turn off the parts a headless run should
        # never touch: MCP servers (slow to start, add tools) and the turn-ended notify hook.
        for name in user_mcp_servers():
            cmd += ["-c", "mcp_servers.%s.enabled=false" % name]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                     **TEXT, **group_kwargs())
        self.wlock, self.plock = threading.Lock(), threading.Lock()
        self.pending, self.subs, self.next_id, self.alive = {}, {}, 0, True
        threading.Thread(target=self._reader, daemon=True).start()
        self.request("initialize", {"clientInfo": {"name": "c2c_img", "title": "c2c_img",
                                                   "version": VERSION}}, timeout=60)
        self._send({"method": "initialized"})

    def _send(self, msg):
        with self.wlock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def request(self, method, params, timeout=60):
        q = queue.Queue(1)
        with self.plock:
            self.next_id += 1
            rid = self.next_id
            self.pending[rid] = q
        try:
            self._send({"id": rid, "method": method, "params": params})
            msg = q.get(timeout=timeout)
        except queue.Empty:
            raise RpcError("%s timed out" % method)
        except OSError as e:
            raise RpcError("app-server pipe closed: %s" % e)
        finally:
            with self.plock:
                self.pending.pop(rid, None)
        if msg is None:
            raise RpcError("app-server exited")
        if "error" in msg:
            raise RpcError("%s: %s" % (method, (msg["error"] or {}).get("message")))
        return msg.get("result") or {}

    def _reader(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "method" in msg and "id" in msg:
                # A request from the server (approvals and the like). With approvalPolicy=never
                # none should arrive; refuse anything that does.
                try:
                    self._send({"id": msg["id"], "error": {"code": -32601, "message": "unsupported by c2c_img"}})
                except OSError:
                    pass
            elif "id" in msg:
                with self.plock:
                    q = self.pending.get(msg["id"])
                if q:
                    q.put(msg)
            else:
                q = self.subs.get((msg.get("params") or {}).get("threadId"))
                if q:
                    q.put(msg)
        self.alive = False
        with self.plock:
            waiters = list(self.pending.values()) + list(self.subs.values())
        for q in waiters:
            q.put(None)

    def fallback_model(self, failed):
        """Pick another visible model to orchestrate with. The orchestrator only has to call
        image_gen, so the choice doesn't affect image quality."""
        if not hasattr(self, "_models"):
            try:
                data = self.request("model/list", {}, timeout=15)
                self._models = [m.get("id") for m in data.get("data", []) if not m.get("hidden")]
            except RpcError:
                self._models = []
        return next((m for m in self._models if m and m != failed), None)

    def run_job(self, job, timeout):
        t0 = time.time()
        out = self._attempt(job, timeout)
        if (not out["src"] and not out["timed_out"] and not out.get("infra_error") and not job.get("model")
                and (info_code(out["error_info"]) == "serverOverloaded"
                     or any(CAPACITY_RE.search(e or "") for e in out["errors"]))):
            alt = self.fallback_model(out.get("model"))
            left = timeout - (time.time() - t0)
            if alt and left > 60:
                retry = self._attempt(dict(job, model=alt), left)
                retry["notes"] = ["%s was at capacity; used %s" % (out.get("model") or "default model", alt)]
                retry["elapsed_s"] = round(time.time() - t0, 1)
                return retry
        return out

    def _attempt(self, job, timeout):
        t0 = time.time()
        workdir = tempfile.mkdtemp(prefix="c2c_img-")
        out = {"engine": "daemon", "src": None, "timed_out": False, "errors": [], "reply": "",
               "failure": None, "error_info": None}
        q, tid, turn_id, replies = queue.Queue(), None, None, []
        try:
            params = {"ephemeral": not job.get("keep_session"), "cwd": workdir,
                      "sandbox": "read-only", "approvalPolicy": "never"}
            if job.get("model"):
                params["model"] = job["model"]
            started = self.request("thread/start", params)
            tid, out["model"] = started["thread"]["id"], started.get("model")
            self.subs[tid] = q
            inputs = [{"type": "text", "text": job["driver"]}] + \
                     [{"type": "localImage", "path": p} for p in job.get("refs", [])]
            turn_id = self.request("turn/start", {"threadId": tid, "input": inputs,
                                                  "effort": "low"})["turn"]["id"]
            while True:
                left = timeout - (time.time() - t0)
                if left <= 0:
                    out["timed_out"] = True
                    break
                try:
                    msg = q.get(timeout=min(left, 5))
                except queue.Empty:
                    if not self.alive:
                        raise RpcError("app-server exited")
                    continue
                if msg is None:
                    raise RpcError("app-server exited")
                method, p = msg.get("method"), msg.get("params") or {}
                if method == "item/completed":
                    item = p.get("item") or {}
                    if item.get("type") == "imageGeneration":
                        out["failure"] = item.get("failure") or out["failure"]
                        saved = item.get("savedPath")
                        if saved and wait_complete(saved):
                            out["src"] = saved
                            break
                    elif item.get("type") == "agentMessage":
                        replies.append(item.get("text") or "")
                elif method in ("error", "turn/completed"):
                    err = p.get("error") if method == "error" else (p.get("turn") or {}).get("error")
                    if err:
                        if err.get("message") and err["message"] not in out["errors"]:
                            out["errors"].append(err["message"])
                        out["error_info"] = err.get("codexErrorInfo") or out["error_info"]
                    if method == "turn/completed":
                        break
            if turn_id and (out["src"] or out["timed_out"]):
                # Same idea as the exec engine: don't let the model re-read the image to say DONE.
                try:
                    self.request("turn/interrupt", {"threadId": tid, "turnId": turn_id}, timeout=10)
                except RpcError:
                    pass
        except RpcError as e:
            out["infra_error"] = str(e)
        finally:
            if tid:
                self.subs.pop(tid, None)
                try:  # unload the thread so a long-lived daemon doesn't hold every image in memory
                    self.request("thread/unsubscribe", {"threadId": tid}, timeout=10)
                except RpcError:
                    pass
            shutil.rmtree(workdir, ignore_errors=True)
        out["reply"] = " ".join(replies).strip()
        out["elapsed_s"] = round(time.time() - t0, 1)
        return out

    def close(self):
        kill_tree(self.proc)


def state_file():
    return os.path.join(state_dir(), "daemon.json")


def write_state(state):
    tmp = state_file() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, state_file())


def read_state():
    try:
        with open(state_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def serve(codex, idle):
    """The daemon process. It owns one warm app-server and serves jobs on 127.0.0.1 with
    token auth. It exits once it has been idle for `idle` seconds, or when app-server dies."""
    lock_fd = try_lock(os.path.join(state_dir(), "daemon.lock"))
    if lock_fd is None:
        return 0  # another daemon is already running
    log = open(os.path.join(state_dir(), "daemon.log"), "w", encoding="utf-8")
    try:
        app = AppServer(codex, log)
    except Exception as e:
        log.write("app-server failed to start: %s\n" % e)
        log.close()
        return 1
    token = secrets.token_hex(16)
    lock = threading.Lock()
    status = {"active": 0, "last": time.time(), "stopping": False}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                req = json.loads(self.rfile.readline().decode("utf-8"))
            except ValueError:
                return
            if not hmac.compare_digest(str(req.get("token", "")), token):
                return
            op = req.get("op")
            if op == "job":
                with lock:
                    status["active"] += 1
                try:
                    res = app.run_job(req["job"], req.get("timeout", 300))
                except Exception as e:  # never leave the client with an empty reply
                    res = {"infra_error": "daemon job crashed: %r" % e}
                finally:
                    with lock:
                        status["active"] -= 1
                        status["last"] = time.time()
            elif op == "stop":
                status["stopping"] = True
                res = {"ok": True}
            else:
                res = {"ok": True, "pid": os.getpid(), "active": status["active"],
                       "idle_s": round(time.time() - status["last"]), "version": VERSION}
            self.wfile.write((json.dumps(res) + "\n").encode("utf-8"))

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    write_state({"pid": os.getpid(), "port": server.server_address[1], "token": token,
                 "fingerprint": daemon_fingerprint(codex), "version": VERSION,
                 "started": datetime.datetime.now().isoformat(timespec="seconds")})

    def watchdog():
        while True:
            time.sleep(2)
            with lock:
                idle_out = status["active"] == 0 and (
                    status["stopping"] or time.time() - status["last"] > idle)
            if idle_out or not app.alive:
                server.shutdown()
                return

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        app.close()
        st = read_state()
        if st and st.get("token") == token:
            try:
                os.remove(state_file())
            except OSError:
                pass
        log.close()
        os.close(lock_fd)
    return 0


def daemon_rpc(st, payload, timeout):
    payload = dict(payload, token=st["token"])
    with socket.create_connection(("127.0.0.1", st["port"]), timeout=5) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8"))


def ping_daemon(st):
    try:
        return daemon_rpc(st, {"op": "ping"}, 3)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ensure_daemon(codex):
    """Return the state of a live daemon for this codex binary, starting one if needed.
    Returns None if none can be started; callers then use the exec engine."""
    fp = daemon_fingerprint(codex)
    st = read_state()
    if st and ping_daemon(st):
        if st.get("fingerprint") == fp and st.get("version") == VERSION:
            return st
        try:  # the codex binary or this script changed: replace the daemon
            daemon_rpc(st, {"op": "stop"}, 3)
        except (OSError, ValueError):
            pass
    def spawn():
        return subprocess.Popen([sys.executable, os.path.abspath(__file__), "--serve", "--codex-bin", codex],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True, **group_kwargs(detached=True))

    proc = spawn()
    deadline = time.time() + 30  # a replaced daemon may still be finishing its last job
    while time.time() < deadline:
        time.sleep(0.3)
        st = read_state()
        if st and st.get("fingerprint") == fp and st.get("version") == VERSION and ping_daemon(st):
            return st
        code = proc.poll()
        if code is None:
            continue
        if code != 0:
            return None  # the daemon failed to start (see daemon.log): fall back to exec now
        # Exit code 0 means another daemon still holds the lock. Usually it's the one being
        # replaced, finishing its last job. Try again once it lets go.
        time.sleep(0.7)
        proc = spawn()
    return None


def run_daemon(job, opts, st):
    payload = {"op": "job", "timeout": opts.timeout,
               "job": {"driver": build_driver_prompt(job), "refs": job["refs"],
                       "keep_session": opts.keep_session, "model": opts.model}}
    try:
        r = daemon_rpc(st, payload, opts.timeout + 30)
    except (OSError, ValueError) as e:
        return {"infra_error": "daemon: %s" % e}
    r.setdefault("stderr_tail", [])
    return r


# ---------- orchestration ----------

FALLBACK_KINDS = ("model", "auth", "unknown")


def run_job(job, opts, codex, daemon_st):
    notes, r = [], None
    if daemon_st:
        r = run_daemon(job, opts, daemon_st)
        notes += r.get("notes") or []
        if r.get("infra_error"):
            notes.append("daemon unavailable (%s); used exec" % r["infra_error"])
            r = None
        elif not r["src"] and classify(r) in FALLBACK_KINDS:
            # The daemon runs on the user's config; lean exec may still work (for example,
            # when an old CLI doesn't know the configured model).
            notes.append("daemon run failed (%s); retried with lean exec" % classify(r))
            r = run_exec(job, opts, codex, lean=True)
    if r is None:
        r = run_exec(job, opts, codex, lean=not opts.use_user_config)
        if not r["src"] and r["engine"] == "exec-lean" and classify(r) in FALLBACK_KINDS:
            # Lean mode can fail when the user's config is required (custom provider, proxy,
            # or credential store). Retry once with the full user config.
            notes.append("lean exec failed (%s); retried with user config" % classify(r))
            r = run_exec(job, opts, codex, lean=False)

    result = {"index": job["index"], "elapsed_s": r["elapsed_s"], "engine": r["engine"]}
    if not r["src"]:
        kind = classify(r)
        detail = "timeout after %ss" % opts.timeout if kind == "timeout" else \
            "; ".join(e for e in r["errors"] if e) or r["reply"] or " ".join(r["stderr_tail"]) \
            or "no image produced"
        result.update(ok=False, error_type=kind, error=detail, hint=HINTS[kind])
        resets = (r.get("failure") or {}).get("resetsAt")
        if resets:
            result["resets_at"] = datetime.datetime.fromtimestamp(resets).isoformat(timespec="minutes")
        if notes:
            result["notes"] = notes
        return result

    out = job["out"]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    src_fmt = os.path.splitext(r["src"])[1].lower().lstrip(".").replace("jpeg", "jpg")
    try:
        if job["size"] is None and job["fmt"] == src_fmt:
            # Move rather than copy: ephemeral runs leave nothing that references the original.
            (shutil.copy2 if opts.keep_session else shutil.move)(r["src"], out)
        else:
            notes += process_image(r["src"], out, job["fmt"], job["size"], opts.quality)
            if not opts.keep_session:
                os.remove(r["src"])
    except Exception as e:  # conversion failed: keep the original rather than lose the image
        fallback = os.path.splitext(out)[0] + os.path.splitext(r["src"])[1]
        shutil.copy2(r["src"], fallback)
        out = fallback
        notes.append("post-processing failed (%s); saved the original instead" % e)
    if not opts.keep_session:
        try:
            os.rmdir(os.path.dirname(r["src"]))
        except OSError:
            pass

    result.update(ok=True, path=out)
    dims = image_size(out)
    if dims:
        result["width"], result["height"] = dims
    if notes:
        result["notes"] = notes
    return result


# ---------- doctor ----------

def doctor(codex):
    checks = []

    def add(name, ok, detail, critical=True):
        checks.append({"check": name, "ok": ok, "detail": detail, "critical": critical})

    add("python", sys.version_info >= (3, 9), "%s on %s" % (sys.version.split()[0], sys.platform))
    if not codex:
        add("codex_cli", False, "not found: install the ChatGPT/Codex desktop app or `npm i -g @openai/codex`")
    else:
        v = cli_version(codex)
        add("codex_cli", bool(v), "%s (%s)" % (codex, ".".join(map(str, v)) if v else "version unknown"))
        p = subprocess.run([codex, "login", "status"], capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=30, **TEXT)
        status = (p.stdout + p.stderr).strip().splitlines()
        status = status[-1] if status else ""
        add("codex_login", "logged in" in status.lower(), status or "unknown")
        p = subprocess.run([codex, "features", "list"], capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=30, **TEXT)
        line = next((l for l in p.stdout.splitlines() if l.startswith("image_generation")), "")
        add("image_generation_feature", bool(line) and "removed" not in line,
            re.sub(r"\s+", " ", line) or "flag not listed (older CLI?)")
    add("codex_output_dir", os.access(codex_home(), os.W_OK),
        os.path.join(codex_home(), "generated_images"))
    caps = converter_caps()
    add("postprocess", caps["resize"],
        "backends=%s; --size/jpg %s; webp %s" % (
            caps["backends"] or "none", "ok" if caps["resize"] else "unavailable (pip install pillow)",
            "ok" if caps["webp"] else "unavailable (pip install pillow, or brew install webp)"),
        critical=False)
    st = read_state()
    info = ping_daemon(st) if st else None
    add("daemon", True, "running (pid %s, idle %ss, active jobs %s)" % (
        info["pid"], info["idle_s"], info["active"]) if info else
        "not running (it starts automatically on the next image)", critical=False)
    add("default_out_dir", True, DEFAULT_OUT_DIR, critical=False)
    return {"ok": all(c["ok"] for c in checks if c["critical"]), "version": VERSION, "checks": checks}


# ---------- main ----------

def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows defaults to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?", help="image prompt; pass '-' to read it from stdin")
    ap.add_argument("-o", "--out", help="output file; the extension sets the format if --format is not given")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                    help="directory used when -o is not given (default: $C2C_OUT_DIR or ~/Pictures/c2c_img)")
    ap.add_argument("-i", "--ref", action="append", default=[],
                    help="input image (edit target or reference); repeatable, in order Image 1, 2, ...")
    ap.add_argument("-t", "--transparent", action="store_true", help="transparent background (png/webp)")
    ap.add_argument("-s", "--size", help="exact output size WxH: generate at the nearest aspect ratio, "
                                         "then cover-resize and center-crop")
    ap.add_argument("-f", "--format", choices=["png", "jpg", "jpeg", "webp"], help="output format")
    ap.add_argument("-q", "--quality", type=int, default=90, help="jpg/webp quality (default 90)")
    ap.add_argument("-n", type=int, default=1, help="variants of the same prompt, run in parallel (max 8)")
    ap.add_argument("--batch", metavar="FILE",
                    help="JSONL or JSON array of jobs ({prompt, out?, ref?, transparent?, size?, format?}); "
                         "'-' reads stdin")
    ap.add_argument("-j", "--jobs", type=int, default=4, help="max parallel Codex runs (default 4)")
    ap.add_argument("--overwrite", action="store_true", help="replace existing files instead of adding -v2, -v3")
    ap.add_argument("-m", "--model", default=os.environ.get("C2C_MODEL"), help="Codex orchestration model")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("C2C_TIMEOUT", "300")),
                    help="seconds per image (default 300)")
    ap.add_argument("--keep-session", action="store_true", help="keep the Codex session and its original file")
    ap.add_argument("--no-daemon", action="store_true", default=os.environ.get("C2C_DAEMON") == "0",
                    help="don't use the warm background app-server; run `codex exec` per image")
    ap.add_argument("--use-user-config", action="store_true",
                    default=os.environ.get("C2C_USE_USER_CONFIG") == "1",
                    help="exec engine: load ~/.codex/config.toml (by default: lean mode, which falls back to this)")
    ap.add_argument("--codex-bin", default=os.environ.get("C2C_CODEX_BIN"))
    ap.add_argument("--doctor", action="store_true", help="check the setup without generating anything")
    ap.add_argument("--stop-daemon", action="store_true", help="stop the background daemon if it is running")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    codex = resolve_codex(args.codex_bin)
    if args.serve:
        return serve(codex, DAEMON_IDLE_S)
    if args.stop_daemon:
        st = read_state()
        stopped = bool(st and ping_daemon(st))
        if stopped:
            daemon_rpc(st, {"op": "stop"}, 5)
        print(json.dumps({"ok": True, "stopped": stopped}))
        return 0
    if args.doctor:
        report = doctor(codex)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if not args.batch:
        if args.prompt == "-":
            args.prompt = sys.stdin.read()
        if not (args.prompt or "").strip():
            ap.error("a prompt (or --batch FILE) is required")
    args.n = max(1, min(args.n, 8))
    try:
        jobs = plan_jobs(args)
    except (ValueError, OSError) as e:
        print(json.dumps({"ok": False, "error_type": "bad_request", "error": str(e)}, ensure_ascii=False))
        return 2

    caps = converter_caps()
    for j in jobs:  # fail before spending quota if the output can't be produced
        need = "webp" if j["fmt"] == "webp" else ("jpg" if j["fmt"] == "jpg" else None)
        if (j["size"] and not caps["resize"]) or (need and not caps[need]):
            print(json.dumps({"ok": False, "error_type": "bad_request",
                              "error": "cannot produce %s%s here (available backends: %s)" % (
                                  j["fmt"], " at %dx%d" % j["size"] if j["size"] else "",
                                  caps["backends"] or "none"),
                              "hint": "Use png (or jpg on macOS), or install Pillow (`pip install pillow`)"
                                      "; for webp, `brew install webp` also works."}))
            return 2
    if not codex:
        print(json.dumps({"ok": False, "error_type": "auth", "error": "codex CLI not found",
                          "hint": "Install Codex (ChatGPT desktop app or `npm i -g @openai/codex`), "
                                  "then run `codex login`."}))
        return 2

    t0, results, lock = time.time(), [], threading.Lock()
    daemon_st = None if args.no_daemon else ensure_daemon(codex)

    def work(job):
        res = run_job(job, args, codex, daemon_st)
        with lock:
            results.append(res)
            state = res.get("path") or "FAILED (%s)" % res.get("error_type")
            print("[c2c_img] %d/%d %s  %.0fs" % (len(results), len(jobs), state, res["elapsed_s"]),
                  file=sys.stderr, flush=True)
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.jobs, 8))) as pool:
        list(pool.map(work, jobs))

    results.sort(key=lambda r: r["index"])
    ok = [r for r in results if r.get("ok")]
    summary = {"ok": len(ok) == len(results), "images": [r["path"] for r in ok],
               "elapsed_s": round(time.time() - t0, 1), "results": results}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok and len(ok) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
