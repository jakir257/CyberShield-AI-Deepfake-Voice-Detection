"""Exercises the new dashboard end to end WITHOUT starting a server.

django.test.Client runs the views in-process, so this proves the template,
the URLs, the upload flow and /recent/ all work together.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "detection.settings")

import django
django.setup()

from django.test import Client

c = Client()
fails = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + ("  " + detail if detail else ""))
    if not ok:
        fails.append(label)


print("\n=== 1. dashboard page ===")
r = c.get("/")
check("GET / returns 200", r.status_code == 200, "got %s" % r.status_code)
html = r.content.decode("utf8", "replace")

for marker in ["CYBERSHIELD", "Upload Audio", "Live Activity",
               "Processing Pipeline", "Detection Model", "dropzone",
               "csrfmiddlewaretoken"]:
    check("page contains %r" % marker, marker in html)

for gone in ["94.8%", "XAI Explanation", "Trusted Voice", "auth-container"]:
    check("old mock %r removed" % gone, gone not in html)


print("\n=== 2. /recent/ endpoint ===")
r = c.get("/recent/")
check("GET /recent/ returns 200", r.status_code == 200, "got %s" % r.status_code)
data = json.loads(r.content)
for key in ("last", "files", "stats", "segment_urls"):
    check("payload has %r" % key, key in data)
check("stats has the four tiles",
      all(k in data["stats"] for k in
          ("total_uploads", "segments", "last_duration", "last_source")))


print("\n=== 3. upload as BROWSER (form carries csrfmiddlewaretoken) ===")
wav = "media/uploads/airplane_chime_x.wav"
with open(wav, "rb") as f:
    r = c.post("/upload-audio/", {"audio": f, "csrfmiddlewaretoken": "x"})
d = json.loads(r.content)
check("upload succeeded", d.get("status") == "success", d.get("message", ""))
for key in ("processed_url", "noise_reduced_url", "trimmed_url",
            "segment_urls", "segment_count", "duration", "trimmed_duration"):
    check("result has %r" % key, key in d)

r = c.get("/recent/")
data = json.loads(r.content)
check("/recent/ tagged it browser",
      data["last"]["source"] == "browser", str(data["last"]["source"]))


print("\n=== 4. upload as PHONE (no form token, like the APK) ===")
m4a = "media/uploads/call_recording.m4a"
if os.path.exists(m4a):
    with open(m4a, "rb") as f:
        r = c.post("/upload-audio/", {"audio": f})
    d = json.loads(r.content)
    check("m4a upload succeeded", d.get("status") == "success", d.get("message", ""))
    r = c.get("/recent/")
    data = json.loads(r.content)
    check("/recent/ tagged it phone",
          data["last"]["source"] == "phone", str(data["last"]["source"]))
    check("stats.total_uploads > 0", data["stats"]["total_uploads"] > 0,
          str(data["stats"]["total_uploads"]))
    check("stats.segments > 0", data["stats"]["segments"] > 0,
          str(data["stats"]["segments"]))
else:
    print("  SKIP  no m4a fixture present")


print("\n=== 5. error path ===")
r = c.get("/upload-audio/")
d = json.loads(r.content)
check("GET on upload endpoint is rejected cleanly", d.get("status") == "error",
      d.get("message", ""))

print("\n" + ("ALL CHECKS PASSED" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
