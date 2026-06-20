# Doubao Podcast Bridge Frontend

## Run

Build output:

```text
dist/DoubaoPodcastBridge/DoubaoPodcastBridge.exe
```

Keep the whole `dist/DoubaoPodcastBridge` folder together. The `_internal` directory is required by the EXE.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\build_frontend_exe.ps1
```

## Package

```powershell
powershell -ExecutionPolicy Bypass -File .\package_release.ps1
```

The portable Windows package is written to:

```text
release/DoubaoPodcastBridge-win64-latest.zip
```

## Notes

- Structured A/B task JSON is saved to `E:\Obsidian\主仓库\90-归档\DoubaoBridgeJson` by default.
- Frontend runtime logs are saved beside the EXE under `logs/`.
- The frontend uses the local Playwright browser cache at `%LOCALAPPDATA%\ms-playwright`.
