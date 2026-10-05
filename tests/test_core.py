"""Tests for the GUI-free core. Run: python3 -m unittest discover -s tests -v"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from simple_git_push import core  # noqa: E402


def make_zip(path: Path, files: dict, prefix: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zi = zipfile.ZipInfo(prefix + name)
            zi.external_attr = (0o755 if name.endswith(".sh") else 0o644) << 16
            zf.writestr(zi, data)
    return path


SAMPLE = {
    "README.md": "# Simple Git Push - GTK tool\n\nHello\n",
    "RELEASE_NOTES.md": "## v1.0.1\n- fixes\n",
    "LICENSE": "MIT",
    "build.sh": "#!/bin/sh\n",
    "src/app.py": "print('hi')\n",
    "dist/simple-git-push_1.0.1_all.deb": "deb-bytes",
}


class Logger:
    def __init__(self):
        self.lines = []

    def __call__(self, kind, text):
        self.lines.append((kind, text))

    def text(self):
        return "\n".join(t for _, t in self.lines)


class ZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_analyze_flat_zip(self):
        z = make_zip(self.tmp / "files.zip", SAMPLE)
        info = core.analyze_zip(z)
        self.assertEqual(info.version, "1.0.1")
        self.assertEqual(info.tag, "v1.0.1")
        self.assertEqual(info.title, "Simple Git Push v1.0.1")
        self.assertEqual(info.notes_file, "RELEASE_NOTES.md")
        self.assertEqual(info.asset, "dist/simple-git-push_1.0.1_all.deb")
        self.assertEqual(info.file_count, 6)

    def test_strips_single_top_folder_and_junk(self):
        data = dict(SAMPLE)
        data["__MACOSX/._x"] = "junk"
        z = make_zip(self.tmp / "p-v2.3.0.zip", data, prefix="proj/")
        info = core.analyze_zip(z)
        self.assertEqual(info.stripped_prefix, "proj")
        self.assertIn("src/app.py", [e.rel for e in info.entries])
        self.assertFalse(any("MACOSX" in e.rel for e in info.entries))

    def test_version_file_wins(self):
        data = dict(SAMPLE)
        data["VERSION"] = "3.4.5\n"
        info = core.analyze_zip(make_zip(self.tmp / "x.zip", data))
        self.assertEqual(info.version, "3.4.5")

    def test_version_from_zip_name(self):
        z = make_zip(self.tmp / "tool-v0.9.2-beta.zip", {"a.txt": "x"})
        self.assertEqual(core.analyze_zip(z).version, "0.9.2-beta")

    def test_rejects_zip_slip(self):
        z = self.tmp / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../evil.txt", "x")
        with self.assertRaises(core.ZipError):
            core.analyze_zip(z)

    def test_bad_zip(self):
        p = self.tmp / "no.zip"
        p.write_text("not a zip")
        with self.assertRaises(core.ZipError):
            core.analyze_zip(p)

    def test_unpack_conflicts_and_modes(self):
        info = core.analyze_zip(make_zip(self.tmp / "f.zip", SAMPLE))
        dev = self.tmp / "dev"
        conflicts, same = core.plan_unpack(info, dev)
        self.assertEqual((conflicts, same), ([], 0))
        written, _ = core.unpack(info, dev, True)
        self.assertEqual(written, 6)
        self.assertTrue(os.access(dev / "build.sh", os.X_OK))
        # identical files are not conflicts
        self.assertEqual(core.plan_unpack(info, dev), ([], 6))
        # change one file -> exactly one conflict
        (dev / "LICENSE").write_text("changed")
        conflicts, same = core.plan_unpack(info, dev)
        self.assertEqual(conflicts, ["LICENSE"])
        self.assertEqual(same, 5)
        # keep existing
        w, s = core.unpack(info, dev, False)
        self.assertEqual((dev / "LICENSE").read_text(), "changed")
        self.assertEqual(w, 0)
        # replace
        core.unpack(info, dev, True)
        self.assertEqual((dev / "LICENSE").read_text(), "MIT")

    def test_file_where_dir_expected(self):
        info = core.analyze_zip(make_zip(self.tmp / "f.zip", SAMPLE))
        dev = self.tmp / "dev"
        dev.mkdir()
        (dev / "src").write_text("i am a file")
        conflicts, _ = core.plan_unpack(info, dev)
        self.assertIn("src", conflicts)
        core.unpack(info, dev, False)
        self.assertTrue((dev / "src").is_file())  # kept
        core.unpack(info, dev, True)
        self.assertTrue((dev / "src" / "app.py").is_file())


class AutoMessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_uses_patch_file_name(self):
        data = dict(SAMPLE)
        data["patches/fix-login-crash.patch"] = "diff"
        info = core.analyze_zip(make_zip(self.tmp / "files.zip", data))
        self.assertEqual(info.patch_files, ["patches/fix-login-crash.patch"])
        self.assertEqual(info.auto_message, "Fix login crash")

    def test_falls_back_to_zip_name(self):
        info = core.analyze_zip(make_zip(self.tmp / "add-export-button-v1.4.0.zip", SAMPLE))
        self.assertEqual(info.patch_files, [])
        self.assertEqual(info.auto_message, "Add export button")

    def test_generic_name_falls_back_to_version(self):
        info = core.analyze_zip(make_zip(self.tmp / "files.zip", SAMPLE))
        self.assertEqual(info.auto_message, "Update to v1.0.1")

    def test_auto_message_is_committed(self):
        info = core.analyze_zip(make_zip(self.tmp / "fix-crash.zip", SAMPLE))
        remote = self.tmp / "r.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
        dev = self.tmp / "dev"
        core.unpack(info, dev, True)
        os.environ.update(GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.x",
                          GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.x")
        msg = core.build_message("auto-generated", auto_text=info.auto_message)
        self.assertTrue(core.commit_and_push(core.Runner(Logger()), dev, str(remote), msg, True))
        self.assertEqual(git(remote, "log", "-1", "--format=%s").strip(), "Fix crash")


def make_folder(root: Path, files: dict) -> Path:
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
        if name.endswith(".sh"):
            p.chmod(0o755)
    return root


class FolderImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = make_folder(self.tmp / "simple-git-push-1.2.2", SAMPLE)

    def test_analyze_folder_like_zip(self):
        info = core.analyze_source(self.src)
        self.assertEqual(info.kind, "folder")
        self.assertEqual(info.source_label, "folder")
        self.assertEqual(info.version, "1.0.1")      # from the .deb name, like a zip
        self.assertEqual(info.tag, "v1.0.1")
        self.assertEqual(info.notes_file, "RELEASE_NOTES.md")
        self.assertEqual(info.asset, "dist/simple-git-push_1.0.1_all.deb")
        self.assertEqual(info.file_count, 6)

    def test_zip_still_goes_through_analyze_source(self):
        z = make_zip(self.tmp / "f.zip", SAMPLE)
        self.assertEqual(core.analyze_source(z).kind, "zip")

    def test_skips_git_and_symlinks(self):
        (self.src / ".git").mkdir()
        (self.src / ".git" / "config").write_text("x")
        os.symlink("/etc/passwd", self.src / "link.txt")
        os.symlink("/etc", self.src / "linkdir")
        rels = [e.rel for e in core.analyze_folder(self.src).entries]
        self.assertFalse(any(r.startswith(".git") for r in rels))
        self.assertNotIn("link.txt", rels)
        self.assertFalse(any(r.startswith("linkdir") for r in rels))

    def test_enters_single_top_folder(self):
        outer = self.tmp / "outer"
        make_folder(outer / "proj", SAMPLE)
        info = core.analyze_folder(outer)
        self.assertEqual(info.stripped_prefix, "proj")
        self.assertIn("src/app.py", [e.rel for e in info.entries])

    def test_empty_and_missing_folder(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(core.ZipError):
            core.analyze_folder(empty)
        with self.assertRaises(core.ZipError):
            core.analyze_folder(self.tmp / "nope")

    def test_copy_conflicts_and_modes(self):
        info = core.analyze_folder(self.src)
        dev = self.tmp / "dev"
        self.assertEqual(core.plan_unpack(info, dev), ([], 0))
        written, _ = core.unpack(info, dev, True)
        self.assertEqual(written, 6)
        self.assertTrue(os.access(dev / "build.sh", os.X_OK))
        self.assertTrue((self.src / "LICENSE").exists())          # the source is only read
        self.assertEqual(core.plan_unpack(info, dev), ([], 6))
        (dev / "LICENSE").write_text("changed")
        self.assertEqual(core.plan_unpack(info, dev)[0], ["LICENSE"])
        core.unpack(info, dev, False)
        self.assertEqual((dev / "LICENSE").read_text(), "changed")
        core.unpack(info, dev, True)
        self.assertEqual((dev / "LICENSE").read_text(), "MIT")

    def test_same_and_nested_location(self):
        info = core.analyze_folder(self.src)
        self.assertTrue(core.same_location(info, self.src))
        self.assertFalse(core.same_location(info, self.tmp / "dev"))
        self.assertTrue(core.inside_source(info, self.src / "sub"))
        self.assertFalse(core.inside_source(info, self.src))
        self.assertFalse(core.inside_source(info, self.tmp / "dev"))
        self.assertFalse(core.inside_source(core.analyze_zip(make_zip(self.tmp / "z.zip", SAMPLE)), self.src))

    def test_auto_message_from_folder_name_and_patch(self):
        info = core.analyze_folder(make_folder(self.tmp / "fix-login-crash-v1.0.2", SAMPLE))
        self.assertEqual(info.auto_message, "Fix login crash")
        data = dict(SAMPLE)
        data["patches/add-export.patch"] = "diff"
        info = core.analyze_folder(make_folder(self.tmp / "p2", data))
        self.assertEqual(info.auto_message, "Add export")


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = core.SETTINGS_PATH
        core.SETTINGS_PATH = self.tmp / "cfg" / "settings.json"

    def tearDown(self):
        core.SETTINGS_PATH = self.old

    def test_default_when_empty(self):
        projects, idx = core.load_projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(idx, 0)
        self.assertEqual(projects[0]["name"], "simple-git-push")

    def test_roundtrip_and_last_project(self):
        a = core.new_project(name="alpha", dev_folder="~/a", repo="https://github.com/x/alpha")
        b = core.new_project(name="beta", dev_folder="~/b", repo="https://github.com/x/beta",
                             commit_type="beta-release", push=False)
        core.save_projects([a, b], 1)
        projects, idx = core.load_projects()
        self.assertEqual([p["name"] for p in projects], ["alpha", "beta"])
        self.assertEqual(idx, 1)
        self.assertEqual(projects[1]["commit_type"], "beta-release")
        self.assertFalse(projects[1]["push"])

    def test_migrates_v1_settings(self):
        core.save_settings({"dev_folder": "~/Devel/old", "repo": "https://github.com/x/oldrepo",
                            "commit_type": "beta-release", "push": False})
        projects, idx = core.load_projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["name"], "oldrepo")
        self.assertEqual(projects[0]["dev_folder"], "~/Devel/old")
        self.assertEqual(projects[0]["commit_type"], "beta-release")

    def test_unique_names_and_bad_data(self):
        core.save_settings({"projects": [{"name": "p"}, {"name": "p"}, "junk", {"name": "q", "bogus": 1}]})
        projects, _ = core.load_projects()
        self.assertEqual([p["name"] for p in projects], ["p", "p (2)", "q"])
        self.assertNotIn("bogus", projects[2])

    def test_corrupt_file(self):
        core.SETTINGS_PATH.parent.mkdir(parents=True)
        core.SETTINGS_PATH.write_text("{not json")
        projects, idx = core.load_projects()
        self.assertEqual((len(projects), idx), (1, 0))


class MetaTests(unittest.TestCase):
    def test_about_constants(self):
        import simple_git_push as pkg
        self.assertEqual(pkg.LICENSE_NAME, "MIT")
        self.assertIsNotNone(core.parse_github_repo(pkg.REPO_URL))
        self.assertRegex(pkg.__version__, r"^\d+\.\d+\.\d+$")
        self.assertIn(pkg.COPYRIGHT_HOLDER, pkg.COPYRIGHT)

    def test_license_file_matches_app_text(self):
        import simple_git_push as pkg
        root = Path(__file__).resolve().parent.parent
        want = core.license_text(pkg.COPYRIGHT_HOLDER, pkg.COPYRIGHT_YEAR)
        self.assertEqual((root / "LICENSE").read_text(encoding="utf-8"), want)


class HelperTests(unittest.TestCase):
    def test_message(self):
        self.assertEqual(core.build_message("stable-release"), "stable-release")
        self.assertEqual(core.build_message("beta-release"), "beta-release")
        self.assertEqual(core.build_message("stable-release", "ignored"), "stable-release")
        self.assertEqual(core.build_message("custom", " hi "), "hi")
        self.assertEqual(core.build_message("custom"), "")
        self.assertEqual(core.build_message("auto-generated", "x", " Fix a bug "), "Fix a bug")
        self.assertEqual(core.build_message("auto-generated"), "")

    def test_message_from_name(self):
        cases = {
            "fix-login-crash_v1.2.3.patch": "Fix login crash",
            "login-crash.diff": "Update login crash",
            "0001-add-dark-mode.patch": "Add dark mode",
            "dir/Update_readme.patch": "Update readme",
            "bump-version-1.2.0-beta.patch": "Bump version",
            "files.zip": "",
            "v2.0.0.zip": "",
        }
        for name, want in cases.items():
            self.assertEqual(core.message_from_name(name), want, name)

    def test_parse_repo(self):
        self.assertEqual(core.parse_github_repo("https://github.com/test/testrepo"), "test/testrepo")
        self.assertEqual(core.parse_github_repo("https://github.com/test/testrepo.git"), "test/testrepo")
        self.assertEqual(core.parse_github_repo("git@github.com:a/b.git"), "a/b")
        self.assertIsNone(core.parse_github_repo("https://example.com/a/b"))


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class GitFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)], check=True,
                       capture_output=True)
        self.dev = self.tmp / "dev"
        info = core.analyze_zip(make_zip(self.tmp / "f.zip", SAMPLE))
        core.unpack(info, self.dev, True)
        os.environ.update(GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.x",
                          GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.x")

    def test_commit_push_excludes_deb(self):
        log = Logger()
        ok = core.commit_and_push(core.Runner(log), self.dev, str(self.remote),
                                  "stable-release", True, True)
        self.assertTrue(ok, log.text())
        self.assertEqual(git(self.remote, "log", "-1", "--format=%s").strip(), "stable-release")
        files = git(self.remote, "ls-tree", "-r", "--name-only", "main")
        self.assertIn("src/app.py", files)
        self.assertNotIn(".deb", files)
        # second run: nothing to commit, still succeeds
        log2 = Logger()
        self.assertTrue(core.commit_and_push(core.Runner(log2), self.dev, str(self.remote),
                                             "again", True, True), log2.text())
        self.assertIn("Nothing new to commit", log2.text())

    def test_no_push(self):
        log = Logger()
        self.assertTrue(core.commit_and_push(core.Runner(log), self.dev, str(self.remote),
                                             "m", False, True), log.text())
        self.assertEqual(git(self.remote, "branch", "--list").strip(), "")

    def test_deb_included_when_not_excluded(self):
        log = Logger()
        core.commit_and_push(core.Runner(log), self.dev, str(self.remote), "m", True, False)
        self.assertIn(".deb", git(self.remote, "ls-tree", "-r", "--name-only", "main"))

    def test_empty_message_fails(self):
        log = Logger()
        self.assertFalse(core.commit_and_push(core.Runner(log), self.dev, "x", " ", True))

    def test_remote_url_is_corrected(self):
        git(self.dev, "init", "-b", "main")
        git(self.dev, "remote", "add", "origin", "https://old.example/x.git")
        log = Logger()
        core.commit_and_push(core.Runner(log), self.dev, str(self.remote), "m", True, True)
        self.assertEqual(git(self.dev, "remote", "get-url", "origin").strip(), str(self.remote))


class ReleaseTests(unittest.TestCase):
    """Uses a fake `gh` that records its arguments."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.txt"
        self.exists = self.tmp / "exists"
        gh = self.bin / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            f'echo "$@" >> {self.calls}\n'
            'if [ "$1 $2" = "release view" ]; then [ -f ' + str(self.exists) + ' ] && exit 0 || exit 1; fi\n'
            "exit 0\n")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{self.old_path}"
        self.dev = self.tmp / "dev"
        core.unpack(core.analyze_zip(make_zip(self.tmp / "f.zip", SAMPLE)), self.dev, True)

    def tearDown(self):
        os.environ["PATH"] = self.old_path

    def release(self, **kw):
        log = Logger()
        args = dict(dev=self.dev, repo="https://github.com/test/testrepo", tag="v1.0.1",
                    title="Simple Git Push v1.0.1", notes_file="RELEASE_NOTES.md",
                    asset="dist/simple-git-push_1.0.1_all.deb")
        args.update(kw)
        return core.create_release(core.Runner(log), **args), log

    def test_creates_release(self):
        ok, log = self.release(prerelease=True)
        self.assertTrue(ok, log.text())
        last = self.calls.read_text().strip().splitlines()[-1]
        self.assertTrue(last.startswith("release create v1.0.1 "))
        for part in ("--repo test/testrepo", "--title Simple Git Push v1.0.1",
                     "--notes-file", "RELEASE_NOTES.md", ".deb", "--prerelease"):
            self.assertIn(part, last)

    def test_existing_release_needs_overwrite(self):
        self.exists.write_text("1")
        ok, log = self.release()
        self.assertFalse(ok)
        self.assertIn("already exists", log.text())
        ok, log = self.release(overwrite=True)
        self.assertTrue(ok, log.text())
        calls = self.calls.read_text()
        self.assertIn("release delete v1.0.1", calls)
        self.assertIn("--cleanup-tag", calls)

    def test_missing_files_and_bad_repo(self):
        self.assertFalse(self.release(asset="nope.deb")[0])
        self.assertFalse(self.release(notes_file="nope.md")[0])
        self.assertFalse(self.release(repo="https://example.com/a/b")[0])


if __name__ == "__main__":
    unittest.main()
