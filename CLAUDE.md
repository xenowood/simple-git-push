# CLAUDE.md

This project was created and tested with the help of Claude (claude.ai), an AI
assistant made by Anthropic.

## What Claude did

- Designed the user interface together with the project owner (mockups first, then the build).
- Wrote the application code, the icon, the desktop entry, the Debian packaging and the documentation.
- Wrote and ran the automated tests for the core logic.

## How it was tested

- `tests/test_core.py` covers zip and folder analysis, version detection, safe unpacking and copying with overwrite handling,
  stored projects, commit and push against a real local bare git repository, and the
  `gh release create` arguments (using a fake `gh`).
- Run them with: `python3 -m unittest discover -s tests -v`
- The GTK 4 window itself could not be started in Claude's sandbox because GTK 4 and libadwaita were not
  installed there. Please check the interface on a real Ubuntu desktop and report anything that looks off.
- The screenshot in `docs/ui.png` is a rendering of the interface, not a capture of the running app.

## Notes for working on this code with Claude

- `simple_git_push/core.py` has no GUI imports. Put logic there, and keep `app.py` thin.
- Every command the app runs is logged to the output pane through `Runner`.
- Keep the project settings format in `core.py` (`load_projects` / `save_projects`) backward compatible.

Licensed under the MIT license (see `LICENSE`).
