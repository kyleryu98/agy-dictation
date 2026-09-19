# macOS development packaging

`../../scripts/build_macos.py` is the source of truth for generated Info.plist and LaunchAgent metadata.
It builds an ad-hoc-signed local helper with a microphone usage description and audio-input entitlement.
It never edits the original Python installation or any existing service.

The generated bundle references the builder machine's Python framework and dependency locations.
It is not a standalone, Developer-ID-signed or notarized release. Generated app bundles and launch
metadata contain local paths and must stay outside Git. Only this entitlement template is tracked.
