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
