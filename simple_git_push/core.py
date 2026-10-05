"""GUI-free core logic for Simple Git Push.

Everything that touches zip files, the file system, git or gh lives here so it
can be unit-tested without a display.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

COMMIT_TYPES = ("stable-release", "beta-release", "auto-generated", "custom")

SETTINGS_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "simple-git-push"
    / "settings.json"
)

_JUNK_PARTS = {"__MACOSX", ".DS_Store"}
_VERSION_RE = re.compile(
    r"(?:^|[^0-9A-Za-z.])v?(\d+\.\d+(?:\.\d+)*(?:-[0-9A-Za-z.]+)?)"
)
_NOTES_NAMES = ("release_notes", "release-notes", "releasenotes", "changelog")


class ZipError(Exception):
    """Raised when a zip file cannot be used."""


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


PROJECT_DEFAULTS = {
    "name": "",
    "dev_folder": "~/Devel/simple-git-push",
    "repo": "https://github.com/owner/repo",
    "commit_type": "stable-release",
    "push": True,
    "exclude_deb": True,
}


def new_project(**kw) -> dict:
    p = dict(PROJECT_DEFAULTS)
    p.update({k: v for k, v in kw.items() if k in PROJECT_DEFAULTS})
    if not p["name"]:
        p["name"] = default_project_name(p["dev_folder"], p["repo"])
    return p


def default_project_name(dev_folder: str, repo: str = "") -> str:
    slug = parse_github_repo(repo) if repo else None
    if slug and "owner/repo" not in slug:
        return slug.split("/")[1]
    return Path(dev_folder.rstrip("/") or "project").name or "project"


def unique_name(name: str, projects: list[dict], ignore: Optional[int] = None) -> str:
    taken = {p["name"] for i, p in enumerate(projects) if i != ignore}
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


def load_projects() -> tuple[list[dict], int]:
    """Return (projects, index of the last used one). Migrates the v1 layout."""
    data = load_settings()
    raw = data.get("projects")
    if raw is None and any(k in data for k in ("dev_folder", "repo")):
        raw = [dict(data, name="")]  # v1: a single unnamed project
    projects = [new_project(**p) for p in (raw or []) if isinstance(p, dict)]
    names: list[dict] = []
    for p in projects:
        p["name"] = unique_name(p["name"], names)
        names.append(p)
    if not projects:
        projects = [new_project()]
    last = data.get("last_project")
    idx = next((i for i, p in enumerate(projects) if p["name"] == last), 0)
    return projects, idx


def save_projects(projects: list[dict], current: int) -> None:
    current = max(0, min(current, len(projects) - 1))
    save_settings({
        "projects": projects,
        "last_project": projects[current]["name"] if projects else "",
    })


def expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path.strip()))).resolve()


# --------------------------------------------------------------------------
# Version / title helpers
# --------------------------------------------------------------------------
def find_version(text: str) -> Optional[str]:
    """Return the first version-looking token in *text* (without leading v)."""
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


_VERBS = {
    "add", "fix", "update", "remove", "delete", "improve", "refactor", "rename", "change",
    "bump", "revert", "merge", "move", "clean", "cleanup", "implement", "support", "enable",
    "disable", "replace", "correct", "adjust", "allow", "use", "make", "drop", "document",
}
_GENERIC = {"patch", "diff", "files", "file", "source", "src", "code", "changes", "update", "new", "final"}


def message_from_name(name: str) -> str:
    """Turn a patch or zip file name into a short commit message.

    fix-login-crash_v1.2.3.patch -> "Fix login crash"
    login-crash.diff             -> "Update login crash"
    """
    stem = PurePosixPath(name).name
    for ext in (".patch", ".diff", ".zip"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
    stem = re.sub(r"(?i)(?:^|[-_. ])v?\d+(?:\.\d+)+(?:-[0-9A-Za-z.]+)?(?=$|[-_. ])", " ", stem)
    stem = re.sub(r"^\d+[-_. ]+", "", stem)  # leading sequence numbers like 0001-
    words = [w for w in re.split(r"[-_.\s]+", stem) if w]
    if words and words[0].lower() in _GENERIC and words[0].lower() != "update":
        words = words[1:]
    while words and words[-1].lower() in ("patch", "diff", "files", "file"):
        words.pop()
    if not words:
        return ""
    text = " ".join(words).lower()
    if words[0].lower() not in _VERBS:
        text = "update " + text
    return text[0].upper() + text[1:]


def build_message(ctype: str, custom_text: str = "", auto_text: str = "") -> str:
    """The commit message is the commit type itself.

    'custom' uses the typed text, 'auto-generated' uses the text made from the patch file name.
    """
    if ctype == "custom":
        return custom_text.strip()
    if ctype == "auto-generated":
        return auto_text.strip()
    return ctype


def parse_github_repo(url: str) -> Optional[str]:
    """'https://github.com/o/r(.git)' or 'git@github.com:o/r.git' -> 'o/r'."""
    m = re.match(
        r"^(?:https?://(?:www\.)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$",
        url.strip(),
    )
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _clean_title(heading: str) -> str:
    heading = re.sub(r"[`*_#]", "", heading).strip()
    heading = re.split(r"\s+[-–—:|]\s+", heading)[0]
    heading = _VERSION_RE.sub("", " " + heading).strip()
    return heading


def _pretty_name(stem: str) -> str:
    stem = _VERSION_RE.sub("", " " + stem)
    return re.sub(r"[-_]+", " ", stem).strip().title()


# --------------------------------------------------------------------------
# Zip analysis
# --------------------------------------------------------------------------
@dataclass
class Entry:
    zip_name: str
    rel: str
    is_dir: bool
    size: int
    crc: int
    mode: int


@dataclass
class ZipInfo:
    path: Path
    entries: list[Entry] = field(default_factory=list)
    version: Optional[str] = None
    title_base: str = ""
    deb_files: list[str] = field(default_factory=list)
    notes_file: Optional[str] = None
    readme: Optional[str] = None
    patch_files: list[str] = field(default_factory=list)
    stripped_prefix: str = ""

    @property
    def file_count(self) -> int:
        return sum(1 for e in self.entries if not e.is_dir)

    @property
    def message_source(self) -> str:
        """The file name the auto-generated commit message is based on."""
        return self.patch_files[0] if self.patch_files else self.path.name

    @property
    def auto_message(self) -> str:
        msg = message_from_name(self.message_source)
        if not msg and self.patch_files:
            msg = message_from_name(self.path.name)
        return msg or (f"Update to {self.tag}" if self.tag else "Update")

    @property
    def tag(self) -> str:
        return f"v{self.version}" if self.version else ""

    @property
    def title(self) -> str:
        base = self.title_base or "Release"
        return f"{base} {self.tag}".strip()

    @property
    def asset(self) -> Optional[str]:
        if not self.deb_files:
            return None
        if self.version:
            for d in self.deb_files:
                if self.version in d:
                    return d
        return sorted(self.deb_files, key=lambda p: (p.count("/"), p))[0]


def _is_junk(name: str) -> bool:
    return any(part in _JUNK_PARTS for part in PurePosixPath(name).parts)


def analyze_zip(path: str | Path) -> ZipInfo:
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ZipError(f"Couldn't open {path.name}: {exc}") from exc

    with zf:
        raw = []
        for zi in zf.infolist():
            if _is_junk(zi.filename):
                continue
            p = PurePosixPath(zi.filename)
            if p.is_absolute() or ".." in p.parts:
                raise ZipError(f"Unsafe path in zip: {zi.filename}")
            mode = (zi.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                continue  # never unpack symlinks
            raw.append((zi, str(p).rstrip("/")))

        files = [r for r in raw if not r[0].is_dir()]
        if not files:
            raise ZipError("The zip file contains no files.")

        # Strip a single common top-level folder (e.g. simple-git-push-v1.0.1/)
        tops = {PurePosixPath(r[1]).parts[0] for r in files}
        prefix = ""
        if len(tops) == 1 and all(len(PurePosixPath(r[1]).parts) > 1 for r in files):
            prefix = next(iter(tops))

        info = ZipInfo(path=path, stripped_prefix=prefix)
        for zi, name in raw:
            rel = name
            if prefix:
                if name == prefix:
                    continue
                rel = name[len(prefix) + 1:]
            if not rel:
                continue
            info.entries.append(
                Entry(zi.filename, rel, zi.is_dir(), zi.file_size, zi.CRC,
                      (zi.external_attr >> 16) & 0xFFFF)
            )

        rels = [e.rel for e in info.entries if not e.is_dir]
        by_depth = lambda p: (p.count("/"), p.lower())  # noqa: E731
        info.patch_files = sorted(
            (r for r in rels if r.lower().endswith((".patch", ".diff"))), key=by_depth)
        info.deb_files = sorted((r for r in rels if r.lower().endswith(".deb")), key=by_depth)

        for r in sorted(rels, key=by_depth):
            base = PurePosixPath(r).name.lower()
            if info.readme is None and base.startswith("readme"):
                info.readme = r
            if info.notes_file is None and base.startswith(_NOTES_NAMES):
                info.notes_file = r

        def read_text(rel: str, limit: int = 8192) -> str:
            entry = next(e for e in info.entries if e.rel == rel)
            return zf.read(entry.zip_name)[:limit].decode("utf-8", "ignore")

        # Version: VERSION file > .deb name > zip name > notes heading
        version = None
        for r in rels:
            if r.upper() in ("VERSION", "VERSION.TXT"):
                version = find_version(read_text(r).strip().splitlines()[0]) if read_text(r).strip() else None
                break
        if not version and info.deb_files:
            m = re.search(r"_(\d[^_]*)_", PurePosixPath(info.deb_files[0]).name)
            version = find_version(m.group(1)) if m else find_version(info.deb_files[0])
        if not version:
            version = find_version(path.stem)
        if not version and info.notes_file:
            for line in read_text(info.notes_file).splitlines()[:25]:
                if line.lstrip().startswith("#"):
                    version = find_version(line)
                    if version:
                        break
        info.version = version

        # Title base: README heading > zip name
        if info.readme:
            for line in read_text(info.readme).splitlines()[:15]:
                if line.startswith("# "):
                    info.title_base = _clean_title(line[2:])
                    break
        if not info.title_base or len(info.title_base) > 40:
            info.title_base = _pretty_name(path.stem)
    return info


# --------------------------------------------------------------------------
# Unpacking
# --------------------------------------------------------------------------
def _crc_of(path: Path) -> int:
    crc = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def _blocking_parent(dev: Path, rel: str) -> Optional[Path]:
    """An existing non-directory that sits where a parent folder must go."""
    cur = dev
    for part in PurePosixPath(rel).parts[:-1]:
        cur = cur / part
        if cur.exists() or cur.is_symlink():
            if not cur.is_dir():
                return cur
        else:
            return None
    return None


def plan_unpack(info: ZipInfo, dev: Path) -> tuple[list[str], int]:
    """Return (conflicting relative paths, number of identical files)."""
    conflicts: list[str] = []
    same = 0
    for e in info.entries:
        blocker = _blocking_parent(dev, e.rel)
        if blocker is not None:
            rel = str(blocker.relative_to(dev))
            if rel not in conflicts:
                conflicts.append(rel)
            continue
        target = dev / e.rel
        if e.is_dir:
            if target.exists() and not target.is_dir():
                conflicts.append(e.rel + "/")
            continue
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_file() and target.stat().st_size == e.size and _crc_of(target) == e.crc:
            same += 1
        else:
            conflicts.append(e.rel)
    return conflicts, same


def unpack(info: ZipInfo, dev: Path, overwrite: bool,
           log: Callable[[str, str], None] = lambda k, t: None) -> tuple[int, int]:
    """Unpack into *dev*. Returns (written, skipped)."""
    dev.mkdir(parents=True, exist_ok=True)
    root = dev.resolve()
    written = skipped = 0
    with zipfile.ZipFile(info.path) as zf:
        for e in info.entries:
            target = (dev / e.rel)
            if root not in target.resolve().parents and target.resolve() != root:
                raise ZipError(f"Refusing to write outside the folder: {e.rel}")
            blocker = _blocking_parent(dev, e.rel)
            if blocker is not None:
                if not overwrite:
                    skipped += 1
                    continue
                blocker.unlink()
            if e.is_dir:
                if target.exists() and not target.is_dir():
                    if not overwrite:
                        skipped += 1
                        continue
                    target.unlink()
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists() or target.is_symlink():
                if (not target.is_symlink() and target.is_file()
                        and target.stat().st_size == e.size and _crc_of(target) == e.crc):
                    skipped += 1
                    continue
                if not overwrite:
                    log("info", f"kept existing {e.rel}")
                    skipped += 1
                    continue
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(e.zip_name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if e.mode & 0o111:
                target.chmod(target.stat().st_mode | (e.mode & 0o111))
            written += 1
    return written, skipped


# --------------------------------------------------------------------------
# Running commands
# --------------------------------------------------------------------------
class Runner:
    """Runs commands and streams their output to log(kind, text).

    kinds: 'cmd' (the command line), 'out', 'err', 'info'
    """

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log

    def run(self, cmd: list[str], cwd: Optional[Path] = None) -> tuple[int, str]:
        self.log("cmd", "$ " + shlex.join(cmd))
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1",
                   GH_NO_UPDATE_NOTIFIER="1")
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd) if cwd else None, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except FileNotFoundError:
            self.log("err", f"{cmd[0]}: command not found")
            return 127, ""
        lines = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            self.log("out", line)
        rc = proc.wait()
        if rc != 0:
            self.log("err", f"exit status {rc}")
        return rc, "\n".join(lines)


# --------------------------------------------------------------------------
# Commit and push
# --------------------------------------------------------------------------
def commit_and_push(r: Runner, dev: Path, repo: str, message: str, push: bool,
                    exclude_deb: bool = True) -> bool:
    if not dev.is_dir():
        r.log("err", f"Folder does not exist: {dev}")
        return False
    if not message.strip():
        r.log("err", "The commit message is empty.")
        return False

    if not (dev / ".git").exists():
        r.log("info", "Not a git repository yet. Initialising it.")
        if r.run(["git", "init", "-b", "main"], dev)[0] != 0:
            return False

    if repo:
        rc, out = r.run(["git", "remote", "get-url", "origin"], dev)
        if rc != 0:
            if r.run(["git", "remote", "add", "origin", repo], dev)[0] != 0:
                return False
        elif out.strip() != repo.strip():
            r.log("info", f"Remote 'origin' pointed to {out.strip()}. Using {repo}.")
            if r.run(["git", "remote", "set-url", "origin", repo], dev)[0] != 0:
                return False

    add = ["git", "add", "-A", "--", "."]
    if exclude_deb:
        add.append(":(exclude)*.deb")
    if r.run(add, dev)[0] != 0:
        return False

    rc, out = r.run(["git", "diff", "--cached", "--name-only"], dev)
    if rc != 0:
        return False
    if not out.strip():
        r.log("info", "Nothing new to commit.")
    elif r.run(["git", "commit", "-m", message], dev)[0] != 0:
        return False

    if push:
        rc, branch = r.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], dev)
        branch = branch.strip()
        if rc != 0 or not branch:
            r.log("err", "There is no commit to push yet.")
            return False
        if r.run(["git", "push", "-u", "origin", branch], dev)[0] != 0:
            return False
        r.log("info", "Push finished.")
    else:
        r.log("info", "Commit finished (push is switched off).")
    return True


# --------------------------------------------------------------------------
# GitHub release
# --------------------------------------------------------------------------
def _resolve(dev: Path, value: str) -> Path:
    p = Path(os.path.expanduser(value.strip()))
    return p if p.is_absolute() else dev / p


def create_release(r: Runner, dev: Path, repo: str, tag: str, title: str,
                   notes_file: str, asset: str, prerelease: bool = False,
                   overwrite: bool = False) -> bool:
    if shutil.which("gh") is None:
        r.log("err", "The GitHub CLI (gh) isn't installed. Run: sudo apt install gh")
        return False
    slug = parse_github_repo(repo)
    if not slug:
        r.log("err", "The repository must be a GitHub URL such as https://github.com/owner/name")
        return False
    if not tag.strip():
        r.log("err", "The release tag is empty.")
        return False

    notes_path = _resolve(dev, notes_file) if notes_file.strip() else None
    asset_path = _resolve(dev, asset) if asset.strip() else None
    if notes_path and not notes_path.is_file():
        r.log("err", f"Notes file not found: {notes_path}")
        return False
    if asset_path and not asset_path.is_file():
        r.log("err", f"Asset not found: {asset_path}")
        return False

    rc, _ = r.run(["gh", "release", "view", tag, "--repo", slug], dev)
    if rc == 0:
        if not overwrite:
            r.log("err", f"Release {tag} already exists. Switch on 'Overwrite if it exists' to replace it.")
            return False
        r.log("info", f"Replacing existing release {tag}.")
        if r.run(["gh", "release", "delete", tag, "--repo", slug, "--yes",
                  "--cleanup-tag"], dev)[0] != 0:
            return False

    cmd = ["gh", "release", "create", tag]
    if asset_path:
        cmd.append(str(asset_path))
    cmd += ["--repo", slug, "--title", title.strip() or tag]
    if notes_path:
        cmd += ["--notes-file", str(notes_path)]
    else:
        cmd += ["--notes", ""]
    if prerelease:
        cmd.append("--prerelease")
    if (dev / ".git").exists():
        rc, branch = r.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], dev)
        if rc == 0 and branch.strip() and branch.strip() != "HEAD":
            cmd += ["--target", branch.strip()]
    if r.run(cmd, dev)[0] != 0:
        return False
    r.log("info", f"Release {tag} created.")
    return True
