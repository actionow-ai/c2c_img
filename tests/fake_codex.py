"""A stand-in for the `codex` CLI, used by test_engines.py so both engines can be tested
without a real Codex login (for example in CI on Linux and Windows).

It implements: --version, login status, features list, the `exec --json` event stream,
and a minimal `app-server` JSON-RPC loop. FAKE_CODEX_MODE selects a scenario:
  ok (default) | quota | capacity | refuse
The "saved" images are written to $CODEX_HOME/generated_images/<thread>/ as real code does.
"""
import json
import os
import struct
import sys
import threading
import time
import uuid
import zlib

MODE = os.environ.get("FAKE_CODEX_MODE", "ok")
HOME = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
DEFAULT_MODEL, ALT_MODEL = "fake-default", "fake-alt"


def write_png(path, w=256, h=256):
    """Write a noisy RGB PNG (noise keeps it above the 1 KB 'complete file' threshold)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return path


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def do_exec(argv):
    prompt = sys.stdin.read()
    tid = str(uuid.uuid4())
    out({"type": "thread.started", "thread_id": tid})
    out({"type": "turn.started"})
    if MODE == "quota":
        out({"type": "error", "message": "You've hit your usage limit. Try again later."})
        out({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}})
        return 1
    if MODE == "refuse":
        out({"type": "item.completed", "item": {"id": "i1", "type": "agent_message",
                                                "text": "FAILED: request violates content policy"}})
        out({"type": "turn.completed", "usage": {}})
        return 0
    if "<image_prompt>" not in prompt:
        out({"type": "error", "message": "driver prompt missing"})
        return 1
    time.sleep(0.3)
    write_png(os.path.join(HOME, "generated_images", tid, "exec-%s.png" % uuid.uuid4()))
    time.sleep(5)  # a real Codex keeps going after the image; c2c_img should stop it early
    out({"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "DONE"}})
    out({"type": "turn.completed", "usage": {}})
    return 0


def do_app_server():
    lock = threading.Lock()

    def send(obj):
        with lock:
            out(obj)

    def run_turn(tid, turn_id, model, text):
        time.sleep(0.3)
        if MODE == "capacity" and model != ALT_MODEL:
            send({"method": "error", "params": {"threadId": tid, "turnId": turn_id, "willRetry": False,
                                                "error": {"message": "Selected model is at capacity. Please try a different model.",
                                                          "codexErrorInfo": "serverOverloaded"}}})
            send({"method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "failed"}}})
            return
        if MODE == "quota":
            item = {"type": "imageGeneration", "id": "ig", "status": "failed", "result": "", "savedPath": None,
                    "failure": {"type": "usageLimitExceeded", "limitId": "images", "resetsAt": int(time.time()) + 3600}}
        elif MODE == "refuse":
            send({"method": "item/completed", "params": {"threadId": tid, "turnId": turn_id, "completedAtMs": 0,
                                                         "item": {"type": "agentMessage", "id": "m", "text": "FAILED: content policy"}}})
            send({"method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "completed"}}})
            return
        else:
            if "<image_prompt>" not in text:
                raise SystemExit("driver prompt missing")
            path = write_png(os.path.join(HOME, "generated_images", tid, "ig-%s.png" % uuid.uuid4()))
            item = {"type": "imageGeneration", "id": "ig", "status": "completed", "result": "AAAA",
                    "savedPath": path, "failure": None, "revisedPrompt": None, "transparentBackground": False}
        send({"method": "item/completed", "params": {"threadId": tid, "turnId": turn_id, "completedAtMs": 0, "item": item}})
        time.sleep(0.5)
        send({"method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "completed"}}})

    threads = {}
    for line in sys.stdin:
        msg = json.loads(line)
        method, mid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if mid is None:
            continue  # client notification (initialized)
        if method == "initialize":
            send({"id": mid, "result": {"userAgent": "fake"}})
        elif method == "thread/start":
            tid = str(uuid.uuid4())
            threads[tid] = p.get("model") or DEFAULT_MODEL
            send({"id": mid, "result": {"thread": {"id": tid}, "model": threads[tid]}})
        elif method == "turn/start":
            turn_id = str(uuid.uuid4())
            text = "".join(i.get("text", "") for i in p.get("input", []) if i.get("type") == "text")
            send({"id": mid, "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}}})
            threading.Thread(target=run_turn, args=(p["threadId"], turn_id, threads.get(p["threadId"]), text),
                             daemon=True).start()
        elif method == "model/list":
            send({"id": mid, "result": {"data": [{"id": DEFAULT_MODEL, "isDefault": True, "hidden": False},
                                                 {"id": ALT_MODEL, "isDefault": False, "hidden": False}]}})
        elif method == "thread/unsubscribe":
            send({"id": mid, "result": {"status": "unsubscribed"}})
        else:
            send({"id": mid, "result": {}})
    return 0


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        print("codex-cli 9.9.9")
    elif argv[:2] == ["login", "status"]:
        print("Logged in using ChatGPT")
    elif argv[:2] == ["features", "list"]:
        print("image_generation                         stable             true")
    elif argv[:1] == ["exec"]:
        return do_exec(argv)
    elif argv[:1] == ["app-server"]:
        if MODE == "no_app_server":  # simulates an old CLI without app-server
            sys.stderr.write("error: unrecognized subcommand 'app-server'\n")
            return 2
        return do_app_server()
    return 0


if __name__ == "__main__":
    sys.exit(main())
