"""Small desktop PDF converter using MinerU cloud and local MinerU 4.1."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone

import requests
from pypdf import PdfReader


API = "https://mineru.net/api/v4"
APP_DIR = Path(__file__).resolve().parent
MODES = ("Auto: cloud, then local if needed", "Cloud only", "Local only")


class ApiError(Exception):
    def __init__(self, code, message):
        self.code = str(code)
        super().__init__(f"MinerU {self.code}: {message}")


class Paused(Exception):
    pass


class ConnectionProblem(RuntimeError):
    pass


def internet_reachable():
    """Probe public endpoints without sending API credentials or PDF data."""
    try:
        response = requests.get("http://www.msftconnecttest.com/connecttest.txt",
                                timeout=(3, 3), allow_redirects=False)
        if response.status_code == 200 and response.text.strip() == "Microsoft Connect Test":
            return True
    except requests.RequestException:
        pass
    try:
        requests.get("https://mineru.net/", timeout=(3, 3), allow_redirects=False)
        return True
    except requests.RequestException:
        return False


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def windows_protect(data, decrypt=False):
    """Protect or recover bytes for the current Windows user with DPAPI."""
    if os.name != "nt":
        raise OSError("Saving API tokens is supported on Windows only.")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    source_bytes = ctypes.create_string_buffer(data, len(data))
    source = DataBlob(len(data), ctypes.cast(source_bytes, ctypes.POINTER(ctypes.c_byte)))
    result = DataBlob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(DataBlob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def token_store_path():
    local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_appdata / "MinerU_PDF_Converter" / "tokens.dpapi"


def load_tokens():
    path = token_store_path()
    return windows_protect(path.read_bytes(), decrypt=True).decode("utf-8") if path.exists() else ""


def save_tokens(tokens):
    path = token_store_path()
    if not tokens.strip():
        path.unlink(missing_ok=True)
        return
    encrypted = windows_protect(tokens.encode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(encrypted)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def output_lock(output):
    """Release automatically on exit or process crash, including on Windows."""
    with (output / ".converter.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another conversion is using this output folder.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def unpack_result(archive, destination):
    """Publish a complete result directory only after basic structural checks."""
    staging = destination.with_name(destination.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                name = member.filename.replace("\\", "/")
                parts = PurePosixPath(name)
                if parts.is_absolute() or ".." in parts.parts or ":" in name:
                    raise ValueError("The result archive contains an unsafe path.")
            bundle.extractall(staging)
        markdowns = sorted(staging.rglob("*.md"))
        markdowns = [path for path in markdowns if path.read_text(encoding="utf-8").strip()]
        json_files = sorted(staging.rglob("*.json"))
        if not markdowns or not json_files:
            raise ValueError("Result must contain non-empty Markdown and structured JSON.")
        for path in json_files:
            read_json(path)
        markdown = next(
            (path for path in markdowns if path.name in {"full.md", "markdown.md"}),
            markdowns[0],
        ).relative_to(staging)
        shutil.rmtree(destination, ignore_errors=True)
        staging.replace(destination)
        return str(markdown)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


class Converter:
    def __init__(self, output, token, mode, tier, local_executable, page_cap,
                 emit, stop=None, poll_seconds=5, wait_seconds=600):
        self.output = Path(output)
        self.tokens = list(dict.fromkeys(part for part in re.split(r"[\s,;]+", token.strip()) if part))
        self.token_index = 0
        self.mode = mode
        self.tier = tier
        self.local_executable = str(local_executable)
        self.page_cap = page_cap
        self.emit = emit
        self.stop = stop or threading.Event()
        self.poll_seconds = poll_seconds
        self.wait_seconds = wait_seconds
        self.limited_tokens = set()
        self.session = requests.Session()
        # The token is only attached to API calls, never signed upload/download URLs.

    def pause_check(self):
        if self.stop.is_set():
            raise Paused("Paused. Click Start / Resume to continue.")

    def wait(self, seconds):
        if self.stop.wait(seconds):
            self.pause_check()

    def api(self, method, path, token, **kwargs):
        for attempt in range(4):
            self.pause_check()
            try:
                response = self.session.request(
                    method, API + path, headers={"Authorization": f"Bearer {token}"},
                    timeout=(20, 60), **kwargs
                )
            except (requests.ConnectionError, requests.Timeout) as error:
                raise ConnectionProblem(
                    "Cloud connection failed or timed out. Saved jobs can be resumed."
                ) from error
            except requests.RequestException as error:
                # In particular, do not repeat a submission whose response was lost.
                raise RuntimeError(
                    "Cloud connection failed. Saved jobs can be resumed. "
                    f"Network error type: {type(error).__name__}."
                ) from error
            if response.status_code == 429:
                if attempt == 3:
                    raise ApiError(429, "Rate limit persists. Resume after a short wait.")
                self.emit("log", "Rate limited; waiting before retrying.")
                self.wait(5 * (attempt + 1))
                continue
            if response.status_code >= 500:
                raise ApiError(response.status_code, "Cloud service is temporarily unavailable.")
            if response.status_code >= 400:
                raise ApiError(response.status_code, "Cloud request rejected; check the API token.")
            data = response.json()
            if str(data.get("code")) != "0":
                raise ApiError(data.get("code"), str(data.get("msg", "Request rejected")))
            return data["data"]

    def usage(self, token):
        account = hashlib.sha256(token.encode()).hexdigest()[:16]
        # This is an app-maintained UTC day counter, not a remote quota query.
        key = datetime.now(timezone.utc).date().isoformat() + "_" + account
        pages = sum(
            job.get("pages", 0)
            for job in (read_json(path) for path in self.output.glob("*/cloud_job*.json"))
            if job.get("usage_day") == key
        )
        return pages, key

    def cloud(self, source, digest, pages, folder):
        job_path = folder / "cloud_job.json"
        job = read_json(job_path)
        if not job:
            if source.stat().st_size > 200_000_000 or pages > 600:
                raise ApiError("file_limit", "This PDF exceeds a cloud file limit.")
            last_limit = ApiError(-60018, "All supplied API tokens reached their limits.")
            order = list(range(self.token_index, len(self.tokens))) + list(range(self.token_index))
            for index in order:
                token = self.tokens[index]
                token_id = hashlib.sha256(token.encode()).hexdigest()[:16]
                if token_id in self.limited_tokens:
                    continue
                used_pages, day = self.usage(token)
                if self.page_cap and used_pages + pages > self.page_cap:
                    self.emit("log", f"API token {index + 1} reached its configured page budget; trying the next token.")
                    last_limit = ApiError("app_page_cap", "All available tokens reached their page budgets or daily limits.")
                    continue
                self.emit("log", f"Requesting cloud upload for {source.name} with API token {index + 1}")
                try:
                    data = self.api("POST", "/file-urls/batch", token, json={
                        "files": [{"name": source.name, "data_id": digest}],
                        "model_version": "vlm", "language": "en",
                        "enable_formula": True, "enable_table": True,
                    })
                except ApiError as error:
                    if error.code != "-60018":
                        raise
                    self.limited_tokens.add(token_id)
                    self.emit("log", f"API token {index + 1} reached its daily limit; trying the next token.")
                    last_limit = error
                    continue
                self.token_index = index
                break
            else:
                raise last_limit
            job = {
                "batch_id": data["batch_id"], "upload_url": data["file_urls"][0],
                "source_sha256": digest, "created_utc": utc_now(), "pages": pages,
                "usage_day": day, "token_id": token_id,
            }
            # Save the reservation before uploading; a restart queries the same batch.
            write_json(job_path, job)

        # Older job records already identify their token through usage_day.
        owner = job.get("token_id") or job.get("usage_day", "").rsplit("_", 1)[-1]
        token = next((value for value in self.tokens
                      if hashlib.sha256(value.encode()).hexdigest()[:16] == owner), None)
        if token is None:
            raise RuntimeError("This saved cloud job needs its original API token. Add that token and resume.")
        deadline = time.monotonic() + self.wait_seconds
        uploaded_this_run = False
        while True:
            self.pause_check()
            try:
                data = self.api("GET", f"/extract-results/batch/{job['batch_id']}", token)
            except ApiError as error:
                if error.code == "-60018":
                    raise RuntimeError("The saved cloud job's account is limited. Job preserved; resume later to check its result.") from error
                raise
            items = data.get("extract_result", [])
            item = next((entry for entry in items if entry.get("data_id") == digest), None)
            if item is None and len(items) == 1:
                item = items[0]
            state = item.get("state") if item else "waiting-file"
            if state == "done":
                self.emit("status", source, "Cloud processing complete")
                return item["full_zip_url"]
            if state == "failed":
                if str(item.get("err_code")) == "-60018":
                    self.limited_tokens.add(owner)
                    self.emit("log", "Cloud job failed with a confirmed daily limit; trying the next API token.")
                    history_id = hashlib.sha256(job["batch_id"].encode()).hexdigest()[:16]
                    job_path.replace(folder / f"cloud_job_limited_{history_id}.json")
                    return self.cloud(source, digest, pages, folder)
                raise RuntimeError(
                    "Cloud conversion failed: " + str(item.get("err_msg", "Unknown reason"))
                    + ". You can select Local only for this PDF."
                )
            if state == "waiting-file" and not uploaded_this_run:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(job["created_utc"])
                if age.total_seconds() > 23 * 3600:
                    raise RuntimeError(
                        "The saved upload link expired. Select Local only, or remove "
                        "this PDF's cloud_job.json to request a fresh cloud upload."
                    )
                self.emit("log", f"Uploading {source.name}")
                self.emit("status", source, "Uploading PDF to MinerU")
                try:
                    with source.open("rb") as pdf:
                        response = self.session.put(job["upload_url"], data=pdf, timeout=(20, 300))
                    response.raise_for_status()
                except (requests.ConnectionError, requests.Timeout) as error:
                    raise ConnectionProblem("Upload connection lost. The cloud job was saved.") from error
                except requests.RequestException as error:
                    raise RuntimeError("Upload interrupted. Resume to check the saved cloud job.") from error
                uploaded_this_run = True
            state_labels = {"waiting-file": "Upload complete — waiting for cloud",
                            "pending": "Queued at MinerU", "running": "Processing at MinerU",
                            "converting": "Preparing cloud output"}
            self.emit("status", source, state_labels.get(state, f"Cloud: {state}"))
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Cloud is still pending after 10 minutes. Job saved; resume to check it."
                )
            self.wait(self.poll_seconds)

    def download(self, url, path):
        partial = path.with_suffix(".download")
        try:
            with self.session.get(url, stream=True, timeout=(20, 120)) as response:
                response.raise_for_status()
                with partial.open("wb") as destination:
                    for chunk in response.iter_content(1024 * 1024):
                        self.pause_check()
                        destination.write(chunk)
            partial.replace(path)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as error:
            raise ConnectionProblem("Result download connection lost. The cloud job was saved.") from error
        except requests.RequestException as error:
            raise RuntimeError("Result download interrupted. Resume to download it again.") from error

    def local(self, source, archive):
        executable = Path(self.local_executable)
        if not executable.is_file():
            raise RuntimeError("Local MinerU is missing. Run INSTALL_LOCAL.cmd or choose mineru-kit.exe.")
        env = os.environ.copy()
        env.update({"MINERU_MODEL_SOURCE": "local", "MINERU_TELEMETRY_DISABLED": "1",
                    "PYTHONUNBUFFERED": "1"})
        # A private configuration prevents a remote VLM in an existing config from
        # overriding local inference. Model files use MinerU's normal model root.
        model_home = Path(env.get("MINERU_HOME", str(Path.home() / ".mineru")))
        config = self.output / "local_config.yaml"
        config.write_text(
            "model:\n  source: local\n  base_dir: "
            + json.dumps(str(model_home / "models"))
            + "\n  small_backend: onnx\n  vlm:\n    engine: llama-cpp\n",
            encoding="utf-8",
        )
        env["MINERU_CONFIG"] = str(config)
        env["MINERU_MODEL_SMALL_BACKEND"] = "onnx"
        env["MINERU_MODEL_VLM_ENGINE"] = "llama-cpp"
        env.pop("MINERU_MODEL_VLM_SERVER_URL", None)
        env.pop("MINERU_API_URL", None)
        env.pop("MINERU_API_KEY", None)
        command = [str(executable), "parse", str(source), "-o", str(archive),
                   "--tier", self.tier, "--format", "zip", "--pages", "all"]
        log_path = archive.parent / "local.log"
        self.emit("log", f"Local {self.tier} conversion started. Details in {log_path}")
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            reader = None

            def show_local_output():
                nonlocal reader
                if reader is None:
                    return
                try:
                    chunk = reader.read()
                except OSError:
                    reader.close()
                    reader = None
                    self.emit("log", "Live local output was interrupted. The local log is still being saved.")
                    return
                if chunk:
                    self.emit("log", chunk.rstrip("\r\n"))

            try:
                try:
                    reader = log_path.open("r", encoding="utf-8", errors="replace")
                except OSError:
                    self.emit("log", "Live local output is unavailable. The local log is still being saved.")
                while process.poll() is None:
                    show_local_output()
                    self.wait(0.5)
                show_local_output()
                if process.returncode:
                    raise RuntimeError(f"Local conversion failed. Read {log_path}")
            finally:
                if reader:
                    reader.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def complete(self, profile):
        try:
            manifest = read_json(profile / "conversion.json")
            files = manifest.get("content_sha256", {})
            return bool(manifest.get("status") == "done" and files and all(
                (profile / "content" / name).is_file()
                and file_hash(profile / "content" / name) == digest
                for name, digest in files.items()
            ))
        except (ValueError, OSError):
            return False

    def convert(self, source):
        self.pause_check()
        digest = file_hash(source)
        stem = re.sub(r"[^\w.-]+", "_", source.stem)[:50] or "paper"
        folder = self.output / f"{stem}_{digest[:16]}"
        folder.mkdir(exist_ok=True)
        profiles = {
            "cloud": folder / "cloud-vlm", "local": folder / f"local-{self.tier}"
        }
        choices = ["cloud", "local"] if self.mode == "auto" else [self.mode]
        for backend in choices:
            if self.complete(profiles[backend]):
                self.emit("status", source, f"Already done ({backend})")
                return
        with source.open("rb") as pdf:
            pages = len(PdfReader(pdf).pages)
        if not pages:
            raise ValueError("The PDF has no pages.")
        backend = self.mode
        download_url = None
        if self.mode != "local":
            try:
                self.emit("status", source, "Connecting to cloud")
                download_url = self.cloud(source, digest, pages, folder)
                backend = "cloud"
            except ConnectionProblem as error:
                if self.mode != "auto":
                    raise
                self.emit("log", f"{error} Switching to local conversion. Any submitted cloud job may still run.")
                backend = "local"
            except ApiError as error:
                if self.mode != "auto" or error.code not in {
                    "-60018", "app_page_cap", "file_limit", "-60005", "-60006"
                }:
                    raise
                self.emit("log", f"{error}. Switching to local conversion.")
                backend = "local"
        profile = profiles[backend]
        profile.mkdir(exist_ok=True)
        archive = profile / "result.zip"
        partial_archive = profile / "result.partial.zip"
        self.emit("status", source, "Downloading result from MinerU" if download_url
                  else "Running local conversion")
        if download_url:
            try:
                self.download(download_url, partial_archive)
            except ConnectionProblem as error:
                if self.mode != "auto":
                    raise
                self.emit("log", f"{error} Switching to local conversion. The cloud job is preserved.")
                backend = "local"
                profile = profiles["local"]
                profile.mkdir(exist_ok=True)
                archive = profile / "result.zip"
                partial_archive = profile / "result.partial.zip"
                self.emit("status", source, "Running local conversion")
        if backend == "local":
            self.pause_check()
            partial_archive.unlink(missing_ok=True)
            self.local(source, partial_archive)
        self.emit("status", source, "Checking and saving output")
        markdown = unpack_result(partial_archive, profile / "content")
        if file_hash(source) != digest:
            raise RuntimeError("The source PDF changed during conversion. Resume using the current PDF.")
        partial_archive.replace(archive)
        markdown_path = profile / "content" / markdown
        write_json(profile / "conversion.json", {
            "status": "done", "source_path": str(source), "source_sha256": digest,
            "pages": pages, "backend": backend,
            "requested_model_or_tier": "vlm" if backend == "cloud" else self.tier,
            "local_executable": self.local_executable if backend == "local" else None,
            "finished_utc": utc_now(), "markdown": "content/" + markdown.replace("\\", "/"),
            "markdown_sha256": file_hash(markdown_path), "archive_sha256": file_hash(archive),
            "content_sha256": {
                path.relative_to(profile / "content").as_posix(): file_hash(path)
                for path in sorted((profile / "content").rglob("*")) if path.is_file()
            },
            "validation": "Non-empty Markdown and parseable JSON; scientific accuracy needs review.",
            "cloud_service_model_revision": "not exposed" if backend == "cloud" else None,
        })
        self.emit("log", f"Saved {markdown_path}")
        self.emit("status", source, f"Done ({backend})")

    def run(self, paths, output_folders=None):
        paths = list(paths)
        original_output = self.output
        default_output = self.output.resolve()
        output_folders = {str(Path(source).resolve()): Path(destination).resolve()
                          for source, destination in (output_folders or {}).items()}
        try:
            with ExitStack() as locks:
                folders = {output_folders.get(str(Path(source).resolve()), default_output)
                           for source in paths} or {default_output}
                for folder in sorted(folders, key=lambda path: str(path).lower()):
                    folder.mkdir(parents=True, exist_ok=True)
                    locks.enter_context(output_lock(folder))
                for source in paths:
                    self.pause_check()
                    self.output = output_folders.get(str(Path(source).resolve()), default_output)
                    self.emit("file_start", source, time.monotonic())
                    try:
                        self.convert(Path(source).resolve())
                    except Paused:
                        self.emit("status", source, "Paused")
                        raise
                    except ApiError as error:
                        self.emit("status", source, "Cloud error")
                        self.emit("log", str(error))
                        # Token and daily limit errors affect every later cloud task.
                        if error.code in {"401", "403", "A0202", "A0211", "-60018", "app_page_cap"}:
                            break
                    except Exception as error:
                        self.emit("status", source, "Needs attention")
                        self.emit("log", f"{Path(source).name}: {error}")
                    finally:
                        self.emit("file_end", source, time.monotonic())
        except Paused as error:
            self.emit("log", str(error))
        finally:
            self.output = original_output
            self.session.close()


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("MinerU PDF to Markdown")
    root.geometry("1120x790")
    root.minsize(900, 650)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text="PDF → Markdown", font=("Segoe UI", 18, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

    mode = tk.StringVar(value=MODES[0])
    tier = tk.StringVar(value="standard")
    dark_theme = tk.BooleanVar(value=True)
    show_tokens = tk.BooleanVar(value=False)
    style = ttk.Style(root)
    style.theme_use("clam")
    saved_tokens = ""
    if os.name == "nt":
        try:
            saved_tokens = load_tokens()
        except (OSError, ValueError, UnicodeError) as error:
            messagebox.showwarning("Saved API tokens", f"Could not load saved tokens: {error}")
    token = tk.StringVar(value=saved_tokens or os.environ.get("MINERU_API_TOKEN", ""))
    output = tk.StringVar(value=str(APP_DIR / "output"))
    executable_name = "mineru-kit.exe" if os.name == "nt" else "mineru-kit"
    beside_python = Path(sys.executable).parent / executable_name
    executable = tk.StringVar(value=str(beside_python) if beside_python.exists()
                              else (shutil.which("mineru-kit") or str(beside_python)))
    cap = tk.StringVar(value="2000")
    events = queue.Queue()
    stop = threading.Event()
    paths = {}
    worker = None
    clock = {"batch_start": None, "batch_end": None, "file_start": None, "file_end": None}
    connectivity = {"reachable": None, "checking": False}
    internet_text = tk.StringVar(value="Internet: checking…")

    def persist_tokens(notify=True):
        try:
            save_tokens(token.get())
        except OSError as error:
            messagebox.showwarning("API tokens", f"Could not save tokens: {error}")
            return False
        if notify:
            message = "Tokens saved for your Windows account." if token.get().strip() else "Saved tokens removed."
            messagebox.showinfo("API tokens", message)
        return True

    def browse_output():
        selected = filedialog.askdirectory(title="Output folder")
        if selected:
            output.set(selected)

    def browse_executable():
        selected = filedialog.askopenfilename(title="Select mineru-kit.exe")
        if selected:
            executable.set(selected)

    ttk.Label(frame, text="Mode").grid(row=1, column=0, sticky="w", pady=4)
    ttk.Combobox(frame, textvariable=mode, values=MODES, state="readonly").grid(
        row=1, column=1, columnspan=2, sticky="ew")
    ttk.Label(frame, text="API tokens").grid(row=2, column=0, sticky="w", pady=4)
    token_entry = ttk.Entry(frame, textvariable=token, show="•")
    token_entry.grid(row=2, column=1, sticky="ew")
    ttk.Button(frame, text="Save tokens", command=persist_tokens).grid(row=2, column=2, padx=(6, 0))
    ttk.Checkbutton(frame, text="Show tokens", variable=show_tokens,
                    command=lambda: token_entry.configure(show="" if show_tokens.get() else "•")).grid(
                        row=3, column=0, sticky="w")
    ttk.Label(frame, text="Separate tokens with ;  •  Saved for this Windows account. Cloud mode uploads PDFs.").grid(
        row=3, column=1, columnspan=2, sticky="w")
    ttk.Label(frame, text="Default output").grid(row=4, column=0, sticky="w", pady=4)
    ttk.Entry(frame, textvariable=output).grid(row=4, column=1, sticky="ew")
    ttk.Button(frame, text="Browse", command=browse_output).grid(row=4, column=2, padx=(6, 0))
    ttk.Label(frame, text="Local MinerU").grid(row=5, column=0, sticky="w", pady=4)
    ttk.Entry(frame, textvariable=executable).grid(row=5, column=1, sticky="ew")
    ttk.Button(frame, text="Browse", command=browse_executable).grid(row=5, column=2, padx=(6, 0))
    options = ttk.Frame(frame)
    options.grid(row=6, column=0, columnspan=3, sticky="w", pady=8)
    ttk.Label(options, text="Local quality").pack(side="left")
    ttk.Combobox(options, textvariable=tier, values=("standard", "advanced", "basic"),
                 width=11, state="readonly").pack(side="left", padx=(6, 20))
    ttk.Label(options, text="Cloud pages / token / UTC day (0 = no cap)").pack(side="left")
    ttk.Entry(options, textvariable=cap, width=7).pack(side="left", padx=6)
    ttk.Label(frame, text="Page budget counts each output folder and token separately; other API use is unknown.").grid(
        row=7, column=0, columnspan=3, sticky="w")
    ttk.Label(frame, textvariable=internet_text, style="Connectivity.TLabel").grid(
        row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))

    body = ttk.Frame(root, padding=(16, 0, 16, 16))
    body.grid(row=1, column=0, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(1, weight=1)
    body.rowconfigure(4, weight=2)
    buttons = ttk.Frame(body)
    buttons.grid(sticky="ew", pady=8)
    queue_buttons = ttk.Frame(buttons)
    queue_buttons.pack(side="top", fill="x")
    run_buttons = ttk.Frame(buttons)
    run_buttons.pack(side="top", fill="x", pady=(5, 0))
    list_frame = ttk.Frame(body)
    list_frame.grid(row=1, column=0, sticky="nsew")
    list_frame.columnconfigure(0, weight=1)
    list_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(list_frame, columns=("path", "output", "status"), show="headings", height=7,
                        selectmode="extended")
    tree.heading("path", text="PDF")
    tree.heading("output", text="Output folder")
    tree.heading("status", text="Status")
    tree.column("path", width=440, minwidth=180)
    tree.column("output", width=400, minwidth=180)
    tree.column("status", width=210, minwidth=120)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    vertical_scroll.grid(row=0, column=1, sticky="ns")
    horizontal_scroll = ttk.Scrollbar(list_frame, orient="horizontal", command=tree.xview)
    horizontal_scroll.grid(row=1, column=0, sticky="ew")
    tree.configure(yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set)
    activity = ttk.Frame(body)
    activity.grid(row=2, column=0, sticky="ew", pady=8)
    activity.columnconfigure(0, weight=1)
    progress = ttk.Label(activity, text="Choose a PDF or a folder, then click Start / Resume.")
    progress.grid(row=0, column=0, sticky="w")
    timing = ttk.Label(activity, text="Batch  00:00:00     Current PDF  00:00:00")
    timing.grid(row=1, column=0, sticky="w", pady=(4, 0))
    ttk.Label(body, text="Live activity", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w")
    log = ScrolledText(body, height=10, wrap="word", state="disabled")
    log.grid(row=4, column=0, sticky="nsew", pady=(4, 0))
    footer = ttk.Frame(body)
    footer.grid(row=5, column=0, sticky="e", pady=(4, 0))
    for label, url in (("MinerU", "https://mineru.net"),
                       ("mojsadafi.ir", "https://mojsadafi.ir"),
                       ("GitHub", "https://github.com/mojjss")):
        link = ttk.Label(footer, text=label, style="Link.TLabel", cursor="hand2")
        link.pack(side="left", padx=(10, 0))
        link.bind("<Button-1>", lambda event, target=url: webbrowser.open(target))

    def update_connectivity_style():
        if dark_theme.get():
            background, colors = "#171c25", {None: "#e5c07b", True: "#8dd6ae", False: "#ff9c9c"}
        else:
            background, colors = "#f3f5f8", {None: "#705500", True: "#17633c", False: "#a52828"}
        style.configure("Connectivity.TLabel", background=background, foreground=colors[connectivity["reachable"]])

    def apply_theme():
        if dark_theme.get():
            background, surface, text, muted, accent = "#171c25", "#242d3b", "#e7edf6", "#b6c2d2", "#77b7ff"
            selection = "#315984"
        else:
            background, surface, text, muted, accent = "#f3f5f8", "#ffffff", "#1a2532", "#526272", "#175eaa"
            selection = "#c8dffa"
        root.configure(background=background)
        style.configure(".", background=background, foreground=text, fieldbackground=surface)
        style.configure("TFrame", background=background)
        style.configure("TLabel", background=background, foreground=text)
        update_connectivity_style()
        style.configure("Link.TLabel", background=background, foreground=accent, font=("Segoe UI", 8))
        style.configure("TCheckbutton", background=background, foreground=text)
        style.configure("TButton", background=surface, foreground=text, bordercolor=muted)
        style.map("TButton", background=[("active", selection)], foreground=[("disabled", muted)])
        style.configure("TEntry", fieldbackground=surface, foreground=text, insertcolor=text)
        style.configure("TCombobox", fieldbackground=surface, background=surface, foreground=text,
                        arrowcolor=text)
        style.map("TCombobox", fieldbackground=[("readonly", surface)], foreground=[("readonly", text)],
                  selectbackground=[("readonly", surface)], selectforeground=[("readonly", text)])
        style.configure("Treeview", background=surface, fieldbackground=surface, foreground=text,
                        bordercolor=background, rowheight=25)
        style.map("Treeview", background=[("selected", selection)], foreground=[("selected", text)])
        style.configure("Treeview.Heading", background=background, foreground=text)
        style.map("Treeview.Heading", background=[("active", selection)])
        root.option_add("*TCombobox*Listbox.background", surface)
        root.option_add("*TCombobox*Listbox.foreground", text)
        root.option_add("*TCombobox*Listbox.selectBackground", selection)
        log.configure(background=surface, foreground=text, insertbackground=text,
                      selectbackground=selection, selectforeground=text)

    ttk.Checkbutton(frame, text="Dark theme", variable=dark_theme, command=apply_theme).grid(
        row=0, column=2, sticky="e", pady=(0, 12))

    def log_message(message):
        stamp = datetime.now().strftime("%H:%M:%S")
        log.configure(state="normal")
        log.insert("end", f"[{stamp}] {message}\n")
        log.see("end")
        log.configure(state="disabled")

    def elapsed(start, end):
        if start is None:
            return "00:00:00"
        seconds = max(0, int((end if end is not None else time.monotonic()) - start))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    def update_timing():
        timing.configure(text=f"Batch  {elapsed(clock['batch_start'], clock['batch_end'])}"
                         f"     Current PDF  {elapsed(clock['file_start'], clock['file_end'])}")
        root.after(250, update_timing)

    def add_files(selected):
        for path in selected:
            resolved = str(Path(path).resolve())
            if resolved not in paths:
                paths[resolved] = tree.insert("", "end", values=(resolved, "Default", "Ready"))

    def choose_files():
        add_files(filedialog.askopenfilenames(filetypes=[("PDF files", "*.pdf")]))

    def choose_folder():
        selected = filedialog.askdirectory(title="PDF folder (includes subfolders)")
        if selected:
            add_files(sorted(path for path in Path(selected).rglob("*")
                             if path.is_file() and path.suffix.lower() == ".pdf"))

    def assign_output():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Select PDFs", "Select one or more PDFs in the list first.")
            return
        folder = filedialog.askdirectory(title="Output folder for selected PDFs")
        if folder:
            for item in selected:
                tree.set(item, "output", str(Path(folder).resolve()))

    def use_default_output():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Select PDFs", "Select one or more PDFs in the list first.")
            return
        for item in selected:
            tree.set(item, "output", "Default")

    def use_source_folder():
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Select PDFs", "Select one or more PDFs in the list first.")
            return
        for item in selected:
            tree.set(item, "output", str(Path(tree.set(item, "path")).parent))

    def clear():
        tree.delete(*tree.get_children())
        paths.clear()

    def emit(*event):
        events.put(event)

    def run_converter(settings, selection, output_folders):
        try:
            Converter(**settings).run(selection, output_folders)
        except Exception as error:
            emit("log", str(error))
        finally:
            emit("finished", time.monotonic())

    def start():
        nonlocal worker
        try:
            page_cap = int(cap.get())
            if page_cap < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Page budget", "Enter zero or a positive whole number.")
            return
        selected_mode = {MODES[0]: "auto", MODES[1]: "cloud", MODES[2]: "local"}[mode.get()]
        if not paths:
            messagebox.showinfo("Choose PDF", "Add at least one PDF first.")
            return
        if selected_mode != "local" and not token.get().strip():
            messagebox.showinfo("API tokens", "Paste one or more MinerU API tokens separated by ;, or select Local only.")
            return
        if not output.get().strip():
            messagebox.showerror("Output folder", "Choose an output folder.")
            return
        if os.name == "nt":
            persist_tokens(notify=False)
        stop.clear()
        clock.update(batch_start=time.monotonic(), batch_end=None, file_start=None, file_end=None)
        for button in editing_buttons:
            button.configure(state="disabled")
        pause_button.configure(state="normal")
        progress.configure(text="Working. Cloud jobs and completed outputs are saved for resume.")
        settings = dict(output=Path(output.get()).resolve(), token=token.get(), mode=selected_mode,
                        tier=tier.get(), local_executable=executable.get(), page_cap=page_cap,
                        emit=emit, stop=stop)
        output_folders = {source: tree.set(item, "output") for source, item in paths.items()
                          if tree.set(item, "output") != "Default"}
        worker = threading.Thread(target=run_converter, args=(settings, list(paths), output_folders), daemon=True)
        worker.start()

    def open_output():
        selected = tree.selection()
        destination = tree.set(selected[0], "output") if selected else "Default"
        folder = Path(output.get() if destination == "Default" else destination).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])

    editing_buttons = []
    for text, command in [("Add PDFs", choose_files), ("Add folder", choose_folder),
                          ("Set selected output", assign_output), ("Use default", use_default_output),
                          ("Use source folder", use_source_folder), ("Clear list", clear)]:
        button = ttk.Button(queue_buttons, text=text, command=command)
        button.pack(side="left", padx=(0, 6))
        editing_buttons.append(button)
    start_button = ttk.Button(run_buttons, text="Start / Resume", command=start)
    start_button.pack(side="left", padx=(0, 6))
    editing_buttons.append(start_button)
    pause_button = ttk.Button(run_buttons, text="Pause", command=stop.set, state="disabled")
    pause_button.pack(side="left", padx=6)
    ttk.Button(run_buttons, text="Open output", command=open_output).pack(side="right")

    def probe_connection():
        try:
            reachable = internet_reachable()
        except Exception:
            reachable = False
        events.put(("connectivity", reachable))

    def check_connection():
        if not connectivity["checking"]:
            connectivity["checking"] = True
            threading.Thread(target=probe_connection, args=(), daemon=True).start()
        root.after(10000, check_connection)

    def consume_events():
        try:
            while True:
                event = events.get_nowait()
                if event[0] == "log":
                    log_message(event[1])
                elif event[0] == "connectivity":
                    connectivity.update(reachable=event[1], checking=False)
                    internet_text.set("Internet: available" if event[1]
                                      else "Internet: unavailable or checks blocked")
                    update_connectivity_style()
                elif event[0] == "status":
                    item = paths.get(str(Path(event[1]).resolve()))
                    if item:
                        previous = tree.set(item, "status")
                        tree.set(item, "status", event[2])
                        progress.configure(text=f"{Path(event[1]).name} — {event[2]}")
                        if previous != event[2]:
                            log_message(f"{Path(event[1]).name} — {event[2]}")
                elif event[0] == "file_start":
                    clock.update(file_start=event[2], file_end=None)
                    log_message(f"Started {Path(event[1]).name}")
                elif event[0] == "file_end":
                    clock["file_end"] = event[2]
                    log_message(f"Time spent on {Path(event[1]).name}: {elapsed(clock['file_start'], event[2])}")
                elif event[0] == "finished":
                    clock["batch_end"] = event[1]
                    for button in editing_buttons:
                        button.configure(state="normal")
                    pause_button.configure(state="disabled")
                    progress.configure(text="Batch ended. Check each PDF's status. Start / Resume retries incomplete work.")
        except queue.Empty:
            pass
        root.after(100, consume_events)

    def close():
        if worker and worker.is_alive():
            stop.set()
            progress.configure(text="Pausing safely before closing. A network call may need time to return.")
            root.after(200, close)
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    apply_theme()
    consume_events()
    update_timing()
    root.after(0, check_connection)
    root.mainloop()


if __name__ == "__main__":
    main()
