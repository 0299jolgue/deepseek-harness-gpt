import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parent.resolve()
DATA = ROOT / "data.json"
WORKSPACE = ROOT / "workspace"
TEMPLATES = ROOT / "templates"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "80"))

JOBS = {}
JOBS_LOCK = threading.Lock()


def defaults():
    return {
        "provider": {
            "base_url": os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            "model": os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
            "api_key": os.getenv("NVIDIA_API_KEY", ""),
        },
        "chats": [],
        "skills": [],
    }


def load():
    d = defaults()
    if DATA.exists():
        try:
            current = json.loads(DATA.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                d.update(current)
        except Exception:
            pass
    else:
        DATA.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return d


def safe_path(name):
    p = (WORKSPACE / str(name or "")).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError("Path is outside the workspace")
    return p


def list_files():
    WORKSPACE.mkdir(exist_ok=True)
    return [
        {"path": str(p.relative_to(WORKSPACE)), "size": p.stat().st_size}
        for p in sorted(WORKSPACE.rglob("*"))
        if p.is_file()
    ]


def file_tool(name, args):
    if name in ("create_file", "write_file"):
        p = safe_path(args.get("path"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content", ""), encoding="utf-8")
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "read_file":
        p = safe_path(args.get("path"))
        return {"ok": True, "path": str(p.relative_to(WORKSPACE)), "content": p.read_text(encoding="utf-8")}
    if name == "delete_file":
        p = safe_path(args.get("path"))
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "create_directory":
        p = safe_path(args.get("path"))
        p.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(p.relative_to(WORKSPACE))}
    if name == "move_file":
        src = safe_path(args.get("source"))
        dest = safe_path(args.get("destination"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    if name == "copy_file":
        src = safe_path(args.get("source"))
        dest = safe_path(args.get("destination"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    if name == "zip_files":
        src = safe_path(args.get("source", "."))
        dest = safe_path(args.get("output", "archive.zip"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            if src.is_file():
                z.write(src, src.name)
            else:
                for p in src.rglob("*"):
                    if p.is_file() and p != dest:
                        z.write(p, p.relative_to(src))
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    if name == "unzip_files":
        src = safe_path(args.get("archive"))
        dest = safe_path(args.get("output", "unzipped"))
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            for member in z.infolist():
                target = (dest / member.filename).resolve()
                if target != WORKSPACE and WORKSPACE not in target.parents:
                    raise ValueError("Unsafe ZIP path")
            z.extractall(dest)
        return {"ok": True, "path": str(dest.relative_to(WORKSPACE))}
    raise ValueError("Unknown file tool")


def job_cancelled(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job["cancelled"])


def call_llm(payload, job_id):
    p = payload.get("provider") or {}
    base = (p.get("base_url") or "").rstrip("/")
    key = p.get("api_key") or os.getenv("NVIDIA_API_KEY", "")
    model = p.get("model") or "meta/llama-3.1-8b-instruct"
    if not base or not key:
        raise RuntimeError("Configure an OpenAI-compatible Base URL and API key.")
    system = '''You are Jolgue AI, a normal conversational AI with a workspace. Be helpful and natural. You can create, edit, read, move, copy, delete and zip/unzip files. Available tools: create_file(path,content), write_file(path,content), read_file(path), delete_file(path), create_directory(path), move_file(source,destination), copy_file(source,destination), zip_files(source,output), unzip_files(archive,output). When you need a file operation, emit exactly one line using <tool>{"name":"create_file","args":{"path":"example.py","content":"print(1)"}}</tool>. Use only workspace-relative paths. After tools are executed, explain what changed clearly.'''
    for s in payload.get("skills", []):
        system += f"\nSkill: {s.get('name')}\n{s.get('instructions')}\n"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}] + payload.get("messages", []),
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    response = None
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["response"] = None
    try:
        response = urllib.request.urlopen(req, timeout=180)
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["response"] = response
        if job_cancelled(job_id):
            response.close()
            raise RuntimeError("Generation stopped")
        raw = response.read()
        if job_cancelled(job_id):
            raise RuntimeError("Generation stopped")
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        with JOBS_LOCK:
            JOBS.pop(job_id, None)


def run_tools(text):
    results = []
    clean = text
    for raw in re.findall(r'<tool>\s*(\{.*?\})\s*</tool>', text, re.S):
        try:
            c = json.loads(raw)
            results.append(file_tool(c["name"], c.get("args", {})))
        except Exception as e:
            results.append({"ok": False, "error": str(e)})
    clean = re.sub(r'<tool>\s*\{.*?\}\s*</tool>', '', clean, flags=re.S).strip()
    return clean, results


class H(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/":
            p = TEMPLATES / "chat.html"
            b = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if self.path == "/api/state":
            self.send_json(load())
            return
        if self.path == "/api/files":
            self.send_json({"files": list_files()})
            return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        if self.path == "/api/state":
            DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            self.send_json({"ok": True})
            return

        if self.path == "/api/files":
            try:
                self.send_json(file_tool(data.get("name"), data.get("args", {})))
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return

        if self.path == "/api/chat/stop":
            job_id = str(data.get("job_id", ""))
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self.send_json({"ok": False, "stopped": False})
                    return
                job["cancelled"] = True
                response = job.get("response")
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
            self.send_json({"ok": True, "stopped": True})
            return

        if self.path == "/api/chat":
            job_id = str(data.get("job_id") or uuid.uuid4())
            with JOBS_LOCK:
                JOBS[job_id] = {"cancelled": False, "response": None}
            try:
                content = call_llm(data, job_id)
                if job_cancelled(job_id):
                    self.send_json({"stopped": True, "content": "Generation stopped.", "tools": [], "files": list_files()}, 499)
                    return
                clean, tools = run_tools(content)
                self.send_json({"content": clean, "tools": tools, "files": list_files(), "job_id": job_id})
            except RuntimeError as e:
                if "stopped" in str(e).lower() or job_cancelled(job_id):
                    self.send_json({"stopped": True, "content": "Generation stopped.", "tools": [], "files": list_files()}, 499)
                else:
                    self.send_json({"error": str(e)}, 500)
            except urllib.error.HTTPError as e:
                self.send_json({"error": f"Provider HTTP {e.code}: {e.read().decode(errors='ignore')}"}, 502)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        self.send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    WORKSPACE.mkdir(exist_ok=True)
    load()
    print(f"Jolgue AI listening on {HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
