# Publishing Callstream.apk

The tag is pushed. The upload needs a browser or the `gh` CLI, neither of which
is available from here — `gh` is not installed and there is no API token, so
this last step is yours. Two minutes.

## Why a Release and not the repo

`*.apk` stays gitignored on purpose:

- it is a **1.4 MB binary**, and a fresh copy would enter history on every
  rebuild — the repo grows permanently, and git cannot compress between builds
- git history is forever; a Release asset can be replaced or deleted
- the source is already pushed, so anyone can rebuild it

A Release gives the phone a stable download link without any of that.

## Upload it

1. Open <https://github.com/sempai-lab/Deepfake-Voice-Detection/releases/new?tag=apk-v1>
2. The tag **apk-v1** is already there — pick it from the dropdown.
3. Title: `CallStream APK — live 4-second segments`
4. Paste the notes below.
5. Drag `Callstream.apk` (project root) into the attachment box.
6. **Publish release.**

The download link will be:

```
https://github.com/sempai-lab/Deepfake-Voice-Detection/releases/download/apk-v1/Callstream.apk
```

Open that on the phone's browser to install. Android will warn about an unknown
source — expected for a debug build.

### With the gh CLI instead

If you install GitHub CLI (`winget install GitHub.cli`), the whole thing is:

```
gh release create apk-v1 "Callstream.apk" ^
   --title "CallStream APK - live 4-second segments" ^
   --notes-file RELEASE_APK.md
```

---

## Release notes to paste

**Debug build. For the Monday demo, not for distribution.**

Records from the phone microphone with the call on speaker, and uploads a
4-second WAV segment **while the recording is still running** — the segments
appear in the dashboard's LIVE SEGMENTS panel as they land.

Android does not expose the call's internal audio to an app, so the call must
be on **speaker** for the microphone to hear the other party. That is a
platform limit, not a bug.

**Needs:** the detection server reachable on the LAN, and its address typed
into the app's server field (e.g. `http://192.168.1.7:8000`).

**Verified in this build:** `flushSegment`, `uploadSegment`, `SEGMENT_BYTES`
and the `/upload-segment/` endpoint are all present in `classes3.dex`. Built
from tag `apk-v1`, `BUILD SUCCESSFUL`, 1.4 MB.

**Signing:** debug key. Android will warn about an unknown source on install.

**Permissions:** `RECORD_AUDIO`, `READ_SMS`, `INTERNET`, plus a foreground
microphone service so recording survives the screen going off.
