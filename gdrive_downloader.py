import html
import os
import queue
import re
import sys
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Google Drive Folder Downloader"
LINK_PLACEHOLDER = "Link hier einfügen ..."
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/json": ".json",
    "application/xml": ".xml",
    "application/octet-stream": "",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/html": ".html",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
}

GOOGLE_APP_LABELS = {
    "document": "Google Doc",
    "spreadsheet": "Google Sheet",
    "presentation": "Google Slides",
    "drawing": "Google Zeichnung",
    "form": "Google Formular",
}

GOOGLE_APP_EXTENSIONS = {
    "document": ".docx",
    "spreadsheet": ".xlsx",
    "presentation": ".pptx",
    "drawing": ".png",
    "form": ".zip",
}

GOOGLE_APP_VIEW_URLS = {
    "document": "https://docs.google.com/document/d/{}/edit",
    "spreadsheet": "https://docs.google.com/spreadsheets/d/{}/edit",
    "presentation": "https://docs.google.com/presentation/d/{}/edit",
    "drawing": "https://docs.google.com/drawings/d/{}/edit",
    "form": "https://docs.google.com/forms/d/{}/edit",
}

GOOGLE_APP_EXPORT_URLS = {
    "document": "https://docs.google.com/document/d/{}/export?format=docx",
    "spreadsheet": "https://docs.google.com/spreadsheets/d/{}/export?format=xlsx",
    "presentation": "https://docs.google.com/presentation/d/{}/export?format=pptx",
    "drawing": "https://docs.google.com/drawings/d/{}/export/png",
    "form": "https://docs.google.com/forms/d/{}/export?format=zip",
}

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def resource_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


class CancelledError(Exception):
    pass


class DownloadError(Exception):
    pass


def make_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retries = Retry(
        total=2,
        connect=2,
        read=0,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def parse_link(text):
    text = (text or "").strip()
    if not text:
        return None, None
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        return "file", text
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", text)
    if match:
        return "folder", match.group(1)
    match = re.search(r"/(document|spreadsheets|presentation|drawings|forms)/d/([A-Za-z0-9_-]+)", text)
    if match:
        app_map = {
            "document": "document",
            "spreadsheets": "spreadsheet",
            "presentation": "presentation",
            "drawings": "drawing",
            "forms": "form",
        }
        return "gfile", (app_map[match.group(1)], match.group(2))
    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", text)
    if match:
        return "file", match.group(1)
    match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", text)
    if match:
        return "file", match.group(1)
    return None, None


def sanitize_component(name):
    name = INVALID_FILENAME_CHARS.sub("_", name or "").strip().rstrip(".")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "unbenannt"
    base = name.split(".")[0].upper()
    if base in RESERVED_NAMES:
        name = "_" + name
    return name[:180]


def sanitize_relpath(relpath):
    parts = [p for p in re.split(r"[\\/]+", relpath or "") if p and p not in (".", "..")]
    parts = [sanitize_component(p) for p in parts]
    return Path(*parts) if parts else Path("unbenannt")


def unique_path(path):
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def format_bytes(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def type_label(item):
    if item.get("kind") == "gfile":
        return GOOGLE_APP_LABELS.get(item.get("app"), "Google-Datei")
    name = item.get("name") or ""
    suffix = Path(name).suffix.lstrip(".").upper()
    if suffix:
        return suffix
    mime = item.get("mime") or ""
    extension = MIME_EXTENSIONS.get(mime, "").lstrip(".")
    return extension.upper() if extension else "Datei"


def extract_title(page):
    match = re.search(r"<title>(.*?)</title>", page, re.I | re.S)
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def classify_entry(href, name, mime):
    folder_match = re.search(r"/folders/([A-Za-z0-9_-]+)", href)
    if folder_match or mime == "application/vnd.google-apps.folder":
        if not folder_match:
            return None
        return {
            "kind": "folder",
            "id": folder_match.group(1),
            "name": name,
            "mime": mime,
            "href": href,
        }
    for path, app in (
        ("/document/d/", "document"),
        ("/spreadsheets/d/", "spreadsheet"),
        ("/presentation/d/", "presentation"),
        ("/drawings/d/", "drawing"),
        ("/forms/d/", "form"),
    ):
        if path in href:
            match = re.search(re.escape(path) + r"([A-Za-z0-9_-]+)", href)
            if not match:
                return None
            return {
                "kind": "gfile",
                "id": match.group(1),
                "name": name,
                "mime": mime,
                "href": href,
                "app": app,
            }
    file_match = re.search(r"/file/d/([A-Za-z0-9_-]+)", href)
    if file_match:
        return {
            "kind": "file",
            "id": file_match.group(1),
            "name": name,
            "mime": mime,
            "href": href,
        }
    id_match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", href)
    if id_match:
        return {
            "kind": "file",
            "id": id_match.group(1),
            "name": name,
            "mime": mime,
            "href": href,
        }
    return None


def parse_folder_page(page):
    folder_title = extract_title(page) or "Google Drive"
    entries = []
    for chunk in re.split(r'<div class="flip-entry" id="entry-', page)[1:]:
        if not re.match(r"[A-Za-z0-9_-]+", chunk):
            continue
        href_match = re.search(r'<a\s+href="([^"]+)"', chunk)
        name_match = re.search(r'<div class="flip-entry-title">(.*?)</div>', chunk, re.S)
        if not href_match or not name_match:
            continue
        name = html.unescape(re.sub(r"<[^>]+>", "", name_match.group(1))).strip()
        href = html.unescape(href_match.group(1))
        mime_match = re.search(r'drive-thirdparty\.googleusercontent\.com/16/type/([^"]+)"', chunk)
        mime = mime_match.group(1) if mime_match else ""
        entry = classify_entry(href, name, mime)
        if entry:
            entries.append(entry)
    if "flip-entries" not in page and not entries:
        raise DownloadError(
            "Der Ordner konnte nicht geladen werden. Ist der Link öffentlich freigegeben?"
        )
    return folder_title, entries


def fetch_folder_entries(session, folder_id):
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
    response = session.get(url, timeout=(20, 60))
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_folder_page(response.text)


def fetch_root_file_name(session, file_id):
    try:
        response = session.get(
            f"https://drive.google.com/file/d/{file_id}/view", timeout=(20, 60)
        )
        response.raise_for_status()
        title = extract_title(response.text)
        title = re.sub(r"\s*[-–]\s*Google Drive\s*$", "", title, flags=re.I).strip()
        if title and title.lower() not in ("google drive", "sign in"):
            return title
    except requests.RequestException:
        pass
    return f"Datei_{file_id}"


def fetch_root_gfile_name(session, app, file_id):
    view_url = GOOGLE_APP_VIEW_URLS.get(app, GOOGLE_APP_VIEW_URLS["document"]).format(file_id)
    try:
        response = session.get(view_url, timeout=(20, 60))
        response.raise_for_status()
        title = extract_title(response.text)
        title = re.sub(
            r"\s*[-–]\s*Google (Docs|Sheets|Slides|Drive|Zeichnungen|Forms)\s*$",
            "",
            title,
            flags=re.I,
        ).strip()
        if title and title.lower() not in ("google drive", "sign in"):
            return title
    except requests.RequestException:
        pass
    return GOOGLE_APP_LABELS.get(app, "Google-Datei")


def run_scan(session, link_type, value, include_subfolders, on_item, status_cb, cancel_event):
    if link_type == "folder":
        visited = set()
        pending = [(value, "", 0)]
        items = []
        root_name = None
        while pending:
            if cancel_event.is_set():
                return root_name or "Google Drive", items, True
            folder_id, prefix, depth = pending.pop(0)
            if folder_id in visited or depth > 25:
                continue
            visited.add(folder_id)
            status_cb("Scanne Ordner ..." if root_name is None else f"Scanne Unterordner ... ({len(items)} Dateien gefunden)")
            title, entries = fetch_folder_entries(session, folder_id)
            if root_name is None:
                root_name = title or "Google Drive"
            for entry in entries:
                if entry["kind"] == "folder":
                    if include_subfolders:
                        pending.append((entry["id"], prefix + entry["name"] + "\\", depth + 1))
                    continue
                item = dict(entry)
                item["relpath"] = prefix + entry["name"]
                items.append(item)
                on_item(item)
            if pending:
                time.sleep(0.15)
        return root_name or "Google Drive", items, False

    if link_type == "gfile":
        app, file_id = value
        name = fetch_root_gfile_name(session, app, file_id)
        item = {
            "kind": "gfile",
            "id": file_id,
            "app": app,
            "name": name,
            "relpath": name,
            "mime": f"application/vnd.google-apps.{app}",
        }
    else:
        file_id = value
        name = fetch_root_file_name(session, file_id)
        item = {
            "kind": "file",
            "id": file_id,
            "name": name,
            "relpath": name,
            "mime": "",
        }
    on_item(item)
    root_name = Path(item["name"]).stem or "Google Drive"
    return root_name, [item], False


class ConfirmFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.in_form = False
        self.inputs = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.in_form = True
            if attributes.get("action"):
                self.action = attributes["action"]
        elif tag == "input" and self.in_form:
            name = attributes.get("name")
            if name:
                self.inputs[name] = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


def parse_confirm_form(page, base_url):
    parser = ConfirmFormParser()
    parser.feed(page)
    if not parser.inputs:
        return None
    action = urljoin(base_url, parser.action or "https://drive.usercontent.google.com/download")
    return action, parser.inputs


def is_confirm_page(response):
    content_type = (response.headers.get("Content-Type") or "").lower()
    disposition = (response.headers.get("Content-Disposition") or "").lower()
    return "text/html" in content_type and "attachment" not in disposition


def open_download_stream(session, url):
    response = session.get(url, stream=True, timeout=(25, 180), allow_redirects=True)
    if response.status_code >= 400:
        response.close()
        raise DownloadError(f"HTTP-Fehler {response.status_code}")
    if is_confirm_page(response):
        page = response.text
        response.close()
        form = parse_confirm_form(page, url)
        if not form:
            lower = page.lower()
            if "quota" in lower:
                raise DownloadError("Google-Download-Limit erreicht. Später erneut versuchen.")
            raise DownloadError(
                "Google hat statt der Datei eine HTML-Seite geliefert. "
                "Vermutlich ist die Datei nicht öffentlich freigegeben."
            )
        action, params = form
        response = session.get(
            action, params=params, stream=True, timeout=(25, 180), allow_redirects=True
        )
        if response.status_code >= 400:
            response.close()
            raise DownloadError(f"HTTP-Fehler {response.status_code}")
        if is_confirm_page(response):
            response.close()
            raise DownloadError("Google verlangt eine Bestätigung, die nicht automatisch möglich war.")
    return response


def filename_from_disposition(value):
    if not value:
        return ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", value, re.I)
    if match:
        from urllib.parse import unquote

        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename="?([^";]+)"?', value, re.I)
    if match:
        return match.group(1).strip()
    return ""


def download_item(session, item, base_dir, cancel_event, progress_cb):
    if item["kind"] == "gfile":
        url = GOOGLE_APP_EXPORT_URLS.get(item.get("app"))
        if not url:
            raise DownloadError("Dieser Google-Dateityp wird nicht unterstützt.")
        url = url.format(item["id"])
    else:
        url = (
            "https://drive.usercontent.google.com/download"
            f"?id={item['id']}&export=download&confirm=t"
        )

    response = open_download_stream(session, url)
    try:
        target = sanitize_relpath(item.get("relpath") or item.get("name") or item["id"])
        content_disposition = filename_from_disposition(
            response.headers.get("Content-Disposition")
        )
        suffix_hint = Path(content_disposition).suffix if content_disposition else ""
        if not target.suffix:
            extension = suffix_hint
            if not extension and item["kind"] == "gfile":
                extension = GOOGLE_APP_EXTENSIONS.get(item.get("app"), "")
            if not extension and item["kind"] == "file":
                extension = MIME_EXTENSIONS.get(item.get("mime") or "", "")
            if extension:
                target = target.with_suffix(extension)

        path = Path(base_dir).joinpath(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path = unique_path(path)
        part_path = path.with_name(path.name + ".part")

        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        try:
            with open(part_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if cancel_event.is_set():
                        raise CancelledError()
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    progress_cb(done, total)
            if cancel_event.is_set():
                raise CancelledError()
            if done == 0:
                raise DownloadError("Die heruntergeladene Datei ist leer.")
            os.replace(part_path, path)
        except Exception:
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return path
    finally:
        response.close()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1000x720")
        self.minsize(820, 560)

        self.session = make_session()
        self.items = []
        self.checked = set()
        self.events = queue.Queue()
        self.busy = False
        self.cancel_event = threading.Event()
        self.root_name = ""
        self._target_is_auto = True
        self._setting_target = False
        self._link_placeholder_active = False

        self.link_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.include_var = tk.BooleanVar(value=True)
        self.open_after_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Bereit. Link einfügen und auf „Scannen“ klicken.")

        self._build_ui()
        self._show_link_placeholder()
        self._set_icon()
        self.after(100, self._poll_events)

    def _set_icon(self):
        icon = resource_path("icon.ico")
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _build_ui(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Placeholder.TEntry", foreground="#777777")

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        link_frame = ttk.Frame(outer)
        link_frame.pack(fill="x")
        ttk.Label(link_frame, text="Google-Drive-Link:").pack(side="left")
        self.link_entry = ttk.Entry(link_frame, textvariable=self.link_var)
        self.link_entry.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.link_entry.bind("<Return>", lambda _event: self.start_scan())
        self.link_entry.bind("<FocusIn>", self._on_link_focus_in)
        self.link_entry.bind("<FocusOut>", self._on_link_focus_out)
        self.btn_paste = ttk.Button(link_frame, text="Einfügen", command=self.paste_link)
        self.btn_paste.pack(side="left", padx=(0, 6))
        self.btn_scan = ttk.Button(link_frame, text="Scannen", command=self.start_scan)
        self.btn_scan.pack(side="left")

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(
            options,
            text="Unterordner einbeziehen",
            variable=self.include_var,
        ).pack(side="left")
        ttk.Checkbutton(
            options,
            text="Nach dem Download Ordner öffnen",
            variable=self.open_after_var,
        ).pack(side="left", padx=(18, 0))

        target_frame = ttk.Frame(outer)
        target_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(target_frame, text="Zielordner:").pack(side="left")
        ttk.Entry(target_frame, textvariable=self.target_var).pack(
            side="left", fill="x", expand=True, padx=(8, 6)
        )
        ttk.Button(target_frame, text="Wählen ...", command=self.choose_target).pack(side="left")
        ttk.Button(target_frame, text="Öffnen", command=self.open_target).pack(
            side="left", padx=(6, 0)
        )

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(10, 6))
        ttk.Button(actions, text="Alles auswählen", command=self.select_all).pack(side="left")
        ttk.Button(actions, text="Auswahl aufheben", command=self.select_none).pack(
            side="left", padx=(6, 0)
        )
        self.selection_label = ttk.Label(actions, text="0 Dateien")
        self.selection_label.pack(side="left", padx=(14, 0))

        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)
        columns = ("check", "name", "type")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="none")
        self.tree.heading("check", text="")
        self.tree.heading("name", text="Datei")
        self.tree.heading("type", text="Typ")
        self.tree.column("check", width=44, minwidth=44, stretch=False, anchor="center")
        self.tree.column("name", width=700, minwidth=320, stretch=True)
        self.tree.column("type", width=130, minwidth=90, stretch=False)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        download_frame = ttk.Frame(outer)
        download_frame.pack(fill="x", pady=(10, 0))
        self.btn_download_selection = ttk.Button(
            download_frame, text="Auswahl herunterladen", command=lambda: self.start_download(False)
        )
        self.btn_download_selection.pack(side="left")
        self.btn_download_all = ttk.Button(
            download_frame, text="Alles herunterladen", command=lambda: self.start_download(True)
        )
        self.btn_download_all.pack(side="left", padx=(6, 0))
        self.btn_cancel = ttk.Button(
            download_frame, text="Abbrechen", command=self.cancel, state="disabled"
        )
        self.btn_cancel.pack(side="left", padx=(6, 0))

        self.progress = ttk.Progressbar(outer, maximum=1000)
        self.progress.pack(fill="x", pady=(10, 4))

        ttk.Label(outer, textvariable=self.status_var, anchor="w").pack(fill="x")

        log_frame = ttk.Frame(outer)
        log_frame.pack(fill="both", pady=(6, 0))
        self.log = tk.Text(log_frame, height=6, wrap="none", state="disabled", font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self._set_busy(False)
        self._update_selection_label()

    def _show_link_placeholder(self):
        if self.link_var.get().strip():
            return
        self._link_placeholder_active = True
        self.link_var.set(LINK_PLACEHOLDER)
        self.link_entry.configure(style="Placeholder.TEntry")

    def _hide_link_placeholder(self):
        if not self._link_placeholder_active:
            return
        self._link_placeholder_active = False
        self.link_var.set("")
        self.link_entry.configure(style="TEntry")

    def _set_link_text(self, text):
        self._link_placeholder_active = False
        self.link_var.set(text)
        self.link_entry.configure(style="TEntry")

    def _on_link_focus_in(self, _event):
        self._hide_link_placeholder()

    def _on_link_focus_out(self, _event):
        if not self.link_var.get().strip():
            self._show_link_placeholder()

    def _on_target_changed(self, *_args):
        if not self._setting_target:
            self._target_is_auto = False

    def _set_target(self, value, auto):
        self._setting_target = True
        self.target_var.set(value)
        self._setting_target = False
        self._target_is_auto = auto

    def paste_link(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if not text:
            messagebox.showwarning(APP_TITLE, "Die Zwischenablage ist leer.")
            return
        self._set_link_text(text)
        self.link_entry.focus_set()
        self.link_entry.icursor("end")

    def choose_target(self):
        current = self.target_var.get().strip()
        initial = current if current and Path(current).exists() else str(Path.home())
        selected = filedialog.askdirectory(title="Zielordner wählen", initialdir=initial)
        if selected:
            self._set_target(selected, False)

    def open_target(self):
        target = Path(self.target_var.get().strip() or (Path.home() / "Downloads"))
        if not target.exists():
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                messagebox.showerror(APP_TITLE, f"Ordner konnte nicht geöffnet werden:\n{error}")
                return
        try:
            os.startfile(str(target))
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"Ordner konnte nicht geöffnet werden:\n{error}")

    def _add_log(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{timestamp}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _update_selection_label(self):
        if self.items:
            self.selection_label.configure(
                text=f"{len(self.checked)} von {len(self.items)} ausgewählt"
            )
        else:
            self.selection_label.configure(text="0 Dateien")

    def _on_tree_click(self, event):
        if self.tree.identify_column(event.x) != "#1":
            return
        row = self.tree.identify_row(event.y)
        if row:
            self._toggle_row(row)

    def _on_tree_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self._toggle_row(row)

    def _toggle_row(self, row):
        try:
            index = int(row)
        except ValueError:
            return
        if index in self.checked:
            self.checked.discard(index)
        else:
            self.checked.add(index)
        self._refresh_row(index)
        self._update_selection_label()

    def _refresh_row(self, index):
        item = self.items[index]
        values = (
            "☑" if index in self.checked else "☐",
            item.get("relpath") or item.get("name") or item["id"],
            type_label(item),
        )
        self.tree.item(str(index), values=values)

    def select_all(self):
        if not self.items:
            return
        self.checked = set(range(len(self.items)))
        for index in self.checked:
            self._refresh_row(index)
        self._update_selection_label()

    def select_none(self):
        self.checked.clear()
        for index in range(len(self.items)):
            self._refresh_row(index)
        self._update_selection_label()

    def _add_item_row(self, item):
        index = len(self.items)
        self.items.append(item)
        self.checked.add(index)
        self.tree.insert(
            "",
            "end",
            iid=str(index),
            values=(
                "☑",
                item.get("relpath") or item.get("name") or item["id"],
                type_label(item),
            ),
        )
        self._update_selection_label()

    def _clear_items(self):
        self.items.clear()
        self.checked.clear()
        self.tree.delete(*self.tree.get_children())
        self._update_selection_label()
        self.progress["value"] = 0

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_scan.configure(state=state)
        self.btn_paste.configure(state=state)
        has_items = bool(self.items)
        self.btn_download_selection.configure(state="normal" if has_items and not busy else "disabled")
        self.btn_download_all.configure(state="normal" if has_items and not busy else "disabled")
        self.btn_cancel.configure(state="normal" if busy else "disabled")

    def _refresh_download_buttons(self):
        has_items = bool(self.items)
        if self.busy:
            self.btn_download_selection.configure(state="disabled")
            self.btn_download_all.configure(state="disabled")
        else:
            self.btn_download_selection.configure(state="normal" if has_items else "disabled")
            self.btn_download_all.configure(state="normal" if has_items else "disabled")

    def start_scan(self):
        if self.busy:
            return
        text = "" if self._link_placeholder_active else self.link_var.get()
        link_type, value = parse_link(text)
        if not link_type:
            messagebox.showwarning(
                APP_TITLE,
                "Bitte einen gültigen Google-Drive-Link einfügen.\n\n"
                "Beispiele:\n"
                "- https://drive.google.com/drive/folders/...\n"
                "- https://drive.google.com/file/d/...\n"
                "- https://docs.google.com/document/d/...",
            )
            return
        self._clear_items()
        self._add_log("Scanne Link ...")
        self.status_var.set("Scanne ...")
        self.cancel_event.clear()
        include_subfolders = self.include_var.get()
        self._set_busy(True)
        thread = threading.Thread(
            target=self._scan_worker,
            args=(link_type, value, include_subfolders),
            daemon=True,
        )
        thread.start()

    def _scan_worker(self, link_type, value, include_subfolders):
        try:
            def on_item(item):
                self.events.put(("add_item", item))

            def status_cb(text):
                self.events.put(("status", text))

            root_name, _items, cancelled = run_scan(
                self.session,
                link_type,
                value,
                include_subfolders,
                on_item,
                status_cb,
                self.cancel_event,
            )
            self.events.put(("scan_finished", root_name, cancelled))
        except requests.RequestException as error:
            self.events.put(("scan_error", f"Netzwerkfehler: {error}"))
        except Exception as error:
            self.events.put(("scan_error", str(error)))

    def _default_target(self, root_name):
        safe_name = sanitize_component(root_name or "Google Drive")
        return str(Path.home() / "Downloads" / "GoogleDrive" / safe_name)

    def start_download(self, all_items):
        if self.busy:
            return
        if not self.items:
            messagebox.showinfo(APP_TITLE, "Es sind noch keine Dateien geladen. Bitte zuerst scannen.")
            return
        if all_items:
            indexes = list(range(len(self.items)))
        else:
            indexes = sorted(self.checked)
        if not indexes:
            messagebox.showinfo(APP_TITLE, "Bitte zuerst Dateien auswählen.")
            return
        target_text = self.target_var.get().strip()
        if not target_text:
            target_text = self._default_target(self.root_name)
            self._set_target(target_text, True)
        target = Path(target_text)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"Zielordner konnte nicht erstellt werden:\n{error}")
            return
        selected = [self.items[index] for index in indexes]
        self._add_log(f"Starte Download von {len(selected)} Datei(en) nach {target}")
        self.status_var.set("Download läuft ...")
        self.progress["value"] = 0
        self.cancel_event.clear()
        self._set_busy(True)
        thread = threading.Thread(
            target=self._download_worker,
            args=(selected, target),
            daemon=True,
        )
        thread.start()

    def _download_worker(self, selected, target):
        ok_count = 0
        fail_count = 0
        cancelled = False
        for index, item in enumerate(selected, start=1):
            if self.cancel_event.is_set():
                cancelled = True
                break
            display_name = item.get("relpath") or item.get("name") or item["id"]
            self.events.put(
                ("download_file", index, len(selected), display_name)
            )
            try:
                def progress_cb(done, total, current_index=index, total_files=len(selected)):
                    self.events.put(
                        ("file_progress", current_index, total_files, done, total)
                    )

                path = download_item(
                    self.session, item, target, self.cancel_event, progress_cb
                )
                ok_count += 1
                self.events.put(("log", f"OK: {path}"))
            except CancelledError:
                cancelled = True
                self.events.put(("log", f"Abgebrochen: {display_name}"))
                break
            except Exception as error:
                fail_count += 1
                self.events.put(("log", f"FEHLER: {display_name} — {error}"))
            time.sleep(0.15)
        if self.cancel_event.is_set():
            cancelled = True
        self.events.put(("download_finished", ok_count, fail_count, cancelled, str(target)))

    def cancel(self):
        if not self.busy:
            return
        self.cancel_event.set()
        self.status_var.set("Wird abgebrochen ...")

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "add_item":
                    self._add_item_row(event[1])
                    self._refresh_download_buttons()
                elif kind == "status":
                    self.status_var.set(event[1])
                elif kind == "log":
                    self._add_log(event[1])
                elif kind == "scan_finished":
                    root_name, cancelled = event[1], event[2]
                    self.root_name = root_name
                    if self._target_is_auto or not self.target_var.get().strip():
                        self._set_target(self._default_target(root_name), True)
                    self._set_busy(False)
                    self._refresh_download_buttons()
                    self.progress["value"] = 0
                    if cancelled:
                        self.status_var.set(
                            f"Scan abgebrochen – {len(self.items)} Datei(en) gefunden."
                        )
                    else:
                        self.status_var.set(
                            f"Fertig gescannt: {len(self.items)} Datei(en) in „{root_name}“."
                        )
                        self._add_log(f"Scan abgeschlossen: {len(self.items)} Datei(en).")
                elif kind == "scan_error":
                    self._set_busy(False)
                    self._refresh_download_buttons()
                    self.progress["value"] = 0
                    self.status_var.set("Scan fehlgeschlagen.")
                    self._add_log(f"FEHLER: {event[1]}")
                    messagebox.showerror(APP_TITLE, f"Scan fehlgeschlagen:\n\n{event[1]}")
                elif kind == "download_file":
                    current, total_files, name = event[1], event[2], event[3]
                    self.status_var.set(
                        f"Lade Datei {current}/{total_files}: {name}"
                    )
                    self.progress["value"] = 0
                elif kind == "file_progress":
                    current, total_files, done, total = event[1], event[2], event[3], event[4]
                    if total > 0:
                        self.progress["value"] = max(0.0, min(1000.0, done / total * 1000.0))
                        self.status_var.set(
                            f"Lade Datei {current}/{total_files}: "
                            f"{format_bytes(done)} von {format_bytes(total)}"
                        )
                    else:
                        self.status_var.set(
                            f"Lade Datei {current}/{total_files}: {format_bytes(done)}"
                        )
                elif kind == "download_finished":
                    ok_count, fail_count, cancelled, target = event[1:5]
                    self._set_busy(False)
                    self._refresh_download_buttons()
                    if cancelled:
                        self.progress["value"] = 0
                        self.status_var.set(
                            f"Abgebrochen – {ok_count} heruntergeladen, {fail_count} fehlgeschlagen."
                        )
                        self._add_log(
                            f"Download abgebrochen: {ok_count} OK, {fail_count} Fehler."
                        )
                        messagebox.showinfo(
                            APP_TITLE,
                            f"Download abgebrochen.\n\n{ok_count} Datei(en) erfolgreich, "
                            f"{fail_count} fehlgeschlagen.",
                        )
                    else:
                        self.progress["value"] = 1000
                        self.status_var.set(
                            f"Fertig – {ok_count} heruntergeladen, {fail_count} fehlgeschlagen."
                        )
                        self._add_log(
                            f"Download abgeschlossen: {ok_count} OK, {fail_count} Fehler."
                        )
                        if self.open_after_var.get() and ok_count > 0:
                            try:
                                os.startfile(target)
                            except OSError:
                                pass
                        messagebox.showinfo(
                            APP_TITLE,
                            f"Download abgeschlossen.\n\n{ok_count} Datei(en) erfolgreich, "
                            f"{fail_count} fehlgeschlagen.\n\nZielordner:\n{target}",
                        )
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def destroy(self):
        self.cancel_event.set()
        super().destroy()


def main():
    app = App()
    app.target_var.trace_add("write", app._on_target_changed)
    app.mainloop()


if __name__ == "__main__":
    main()
