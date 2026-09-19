# AGY Dictation

- Keep changes within this repository. Do not alter a user's installed dictation service.
- No microphone, real typing, permission prompts, or login changes in automated tests.
- Separate provider transcription from OS-specific HUD, hotkeys and text insertion.
- Never store or print provider credentials, raw audio, or transcript contents in logs.
- Never substitute a clipboard or foreground-app switch for direct cursor insertion.
- Preserve the user's text if capture, finalization, or focus verification fails.
- Keep insertion lightweight: bind the destination when recording stops, validate
  once before typing, and stop on user interaction or a changed target. Do not add
  per-chunk text reconciliation, caret-echo polling, or automatic input retries.
- Windows support is not implemented. Do not claim all-app, unlimited, or free-account
  validation without matching evidence.
- Build scripts stage artifacts only; installation and public GitHub publication are separate actions.
- Run `python -m unittest discover -s tests` and `ruff check .` for relevant changes.
