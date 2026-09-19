# macOS development packaging

`../../scripts/build_macos.py` is the source of truth for generated Info.plist and LaunchAgent metadata.
It builds an ad-hoc-signed local helper with a microphone usage description and audio-input entitlement.
It never edits the original Python installation or any existing service.

The generated bundle references the builder machine's Python framework and dependency locations.
It is not a standalone, Developer-ID-signed or notarized release. Generated app bundles and launch
metadata contain local paths and must stay outside Git. Only this entitlement template is tracked.

Generated runtime settings are shared by the LaunchAgent and engine bootstrap, so custom CLI/data locations remain consistent. `engine_bootstrap.py --check` imports the packaged engine without starting its microphone, UI, or socket server.

Use `scripts/manage_macos.py` for explicit installation and lifecycle actions. Its default mode is read-only. Do not invoke `--apply` against a user's real installation during automated tests.
