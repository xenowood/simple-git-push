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
