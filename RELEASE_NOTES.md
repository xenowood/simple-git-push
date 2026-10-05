## v1.2.2

- New **Import from folder** button next to **Open file**. A folder is read like a zip and its files are
  copied into the development folder, with the same overwrite question. You can also drop a folder on the window.
- Folder imports skip `.git` and symbolic links, never change the source, and refuse a development folder that
  lies inside the source folder. Picking the development folder itself only reads the version and release fields.
- New About dialog with **About** and **License** tabs. The "Report an issue" link is gone.
- Repository link, copyright and `LICENSE` now name xenowood.

## v1.2.1

- New **About** button (info icon in the title bar) with version, repository link, issue link,
  license (MIT) and credits.
- Projects are locked. The **pencil** button next to add and delete unlocks the project name, folder and
  repository for editing, and the check mark saves and locks them again.
- New **Clear** button next to the zip buttons: removes the loaded zip, empties the commit message and the
  release fields, and switches off pre-release and overwrite. Files on disk are not touched.

## v1.2.0

- New commit type `auto-generated`: the commit message is made from the name of the patch file in the
  zip (`.patch` or `.diff`), or from the zip file name if there is no patch file.

## v1.1.1

- The commit message is now just the commit type (`stable-release` or `beta-release`), without the
  version. `custom` still lets you type your own text.
- Removed the version field from the commit section. The release tag is set in the release section.
- The app icon is scaled to 90% so it sits better in the app grid and the dock.

## v1.1.0

- Added stored projects with a dropdown. Each project keeps its development folder, repository,
  commit type and options. The last used project opens on start.
- Settings from v1.0.0 are migrated into a first project automatically.
- The GitHub release section can be overridden field by field (tag, title, notes file, asset),
  with a reset button per field and an option to replace an existing release.
- New icon.
- Fixed: a file in the way of a folder crashed unpacking.
- Fixed: an excluded `.deb` made the commit step fail when there was nothing else to commit.

## v1.0.0

- First version: import a zip by drag and drop or file dialog, unpack with overwrite prompt,
  commit and push, create a GitHub release, command output pane.
