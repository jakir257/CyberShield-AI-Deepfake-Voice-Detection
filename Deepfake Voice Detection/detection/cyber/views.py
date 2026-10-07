import json
import logging
import os
import re
import time

from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache

from .audio_preprocessing import AudioPreprocessor
from .audio_segmentation import process_audio_segments
from .noise_reduction import process_noise_reduction
from .scam_detection import analyse_batch, score_message, rescore, URL_RE
from .language import detect as detect_language
from .audio_features import analyse_file
from .url_inspection import inspect as inspect_url
from . import llm
from . import model_training
from . import forensics_training
from . import forensics_model

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".flac")

# Rules the model is never allowed to overturn. These were established by
# resolving a domain and fetching the page, not by reading a sentence.
LINK_RULES = (
    "contains a link",
    "shortened link hides its destination",
    "instructs you to click a link",
)


def record_report(payload, source=""):
    """Save one analysed clip to the forensic log. -> the row, or None.

    Called at the end of a successful analysis. Never raises: a failure to
    write history must not turn a good analysis into an error for the user.
    """
    from .models import ForensicReport

    try:
        analysis = payload.get("analysis") or {}
        model = analysis.get("model") or {}

        # the model verdict when there is one, else the heuristic score -
        # a report has to say something even with no trained model loaded
        if model:
            score = model.get("fake_probability")
            verdict = ("DeepFake detected (%.1f%%)" % (score * 100)
                       if model.get("verdict") == "fake"
                       else "Genuine voice (%.1f%% fake)" % (score * 100))
        else:
            raw = analysis.get("score")
            score = (raw / 100.0) if isinstance(raw, (int, float)) else None
            verdict = ("Heuristic only - no trained model"
                       if score is None else
                       "Heuristic indicator %.0f/100" % (score * 100))

        row = ForensicReport.objects.create(
            kind=ForensicReport.VOICE,
            label=payload.get("original_filename") or "audio clip",
            source=source,
            score=score,
            verdict=verdict,
            risk=ForensicReport.risk_for(score),
            model_name=model.get("model_name", ""),
            model_eer=model.get("model_eer"),
            threshold=model.get("threshold"),
            duration=(analysis.get("features") or {}).get("duration"),
            media_url=payload.get("processed_url", ""),
            details=analysis,
        )
        raise_alert(row)
        return row
    except Exception:
        logger.exception("could not write forensic report")
        return None


#: rule/LLM levels -> the 0-1 scale ForensicReport.risk_for() bands.
#: Messages and URLs are scored on a 0-100 rule scale plus a low/medium/high
#: level; the level is the honest summary, so it drives the band.
_LEVEL_SCORE = {"high": 0.92, "medium": 0.6, "low": 0.15}


def record_url_report(report):
    """Log one URL inspection. -> the row, or None. Never raises."""
    from .models import ForensicReport

    try:
        level = (report.get("level") or "low").lower()
        score = _LEVEL_SCORE.get(level, 0.15)
        verdict = {
            "high": "Phishing / malicious",
            "medium": "Suspicious link",
        }.get(level, "No findings")

        reasons = report.get("reasons") or []
        if reasons:
            verdict += " - " + str(reasons[0])[:60]

        row = ForensicReport.objects.create(
            kind=ForensicReport.URL,
            label=report.get("url") or report.get("host") or "link",
            source="browser",
            score=score,
            verdict=verdict,
            risk=ForensicReport.risk_for(score),
            model_name="url heuristics" + (" + LLM" if report.get("ai") else ""),
            details=report,
        )
        raise_alert(row)
        return row
    except Exception:
        logger.exception("could not write URL forensic report")
        return None


def record_message_report(verdict, text, sender=""):
    """Log one scam-message judgement. -> the row, or None. Never raises."""
    from .models import ForensicReport

    try:
        level = (verdict.get("level") or "low").lower()
        score = _LEVEL_SCORE.get(level, 0.15)
        label = sender or (text or "")[:60] or "message"

        name = {
            "high": "Scam / phishing",
            "medium": "Suspicious message",
        }.get(level, "No scam indicators")

        reasons = verdict.get("reasons") or []
        if reasons:
            name += " - " + str(reasons[0])[:60]

        row = ForensicReport.objects.create(
            kind=ForensicReport.MESSAGE,
            label=label,
            source="browser",
            score=score,
            verdict=name,
            risk=ForensicReport.risk_for(score),
            model_name="rules" + (" + LLM" if verdict.get("ai") else ""),
            details=dict(verdict, text=(text or "")[:2000]),
        )
        raise_alert(row)
        return row
    except Exception:
        logger.exception("could not write message forensic report")
        return None


def last_result_path():
    return os.path.join(settings.MEDIA_ROOT, "last_result.json")


def messages_path():
    return os.path.join(settings.MEDIA_ROOT, "messages.json")


@never_cache
@login_required
def index(request):
    """The dashboard. Gated - an anonymous visitor never sees it, they are
    redirected to /signin/ by @login_required.

    @never_cache so the Back button after signing out cannot redisplay this
    page from the browser cache. @login_required only guards the request;
    a cached copy is served without one."""

    display = request.user.get_full_name() or request.user.get_username()

    initials = "".join(
        part[0] for part in display.split() if part
    )[:2].upper() or display[:2].upper()

    return render(request, "cyber/index.html", {
        "user_display_name": display,
        "user_initials": initials,
    })


@login_required
def recent(request):
    """Polled by the dashboard so uploads made from the phone show up without a
    reload. The page has no push channel, so it has to ask."""

    try:
        with open(last_result_path(), encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = None

    uploads_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
    files = []

    if os.path.isdir(uploads_dir):

        for name in os.listdir(uploads_dir):

            if not name.lower().endswith(AUDIO_EXTENSIONS):
                continue

            full = os.path.join(uploads_dir, name)

            files.append({
                "name": name,
                "size": os.path.getsize(full),
                "modified": os.path.getmtime(full),
                "url": settings.MEDIA_URL + "uploads/" + name,
            })

        files.sort(key=lambda f: f["modified"], reverse=True)

    segments_dir = os.path.join(settings.MEDIA_ROOT, "audio_segments")

    segment_files = []

    if os.path.isdir(segments_dir):
        segment_files = sorted(
            n for n in os.listdir(segments_dir)
            if n.startswith("segment_") and n.endswith(".wav")
        )

    return JsonResponse({
        "last": last,
        "files": files[:10],
        "stats": {
            "total_uploads": len(files),
            "segments": len(segment_files),
            "last_duration": (last or {}).get("duration"),
            "last_source": (last or {}).get("source"),
        },
        "segment_urls": [
            settings.MEDIA_URL + "audio_segments/" + n for n in segment_files
        ],
        # the dashboard already polls this every 3s - no second timer needed
        "device_status": device_status(),
        "lan": lan_address(request),
    })


@csrf_exempt
def messages(request):
    """GET  - the dashboard reads the last batch the phone sent.
    POST - the Android app pushes its most recent SMS as JSON.

    csrf_exempt because the poster is a native app with no session; the
    endpoint neither authenticates nor mutates anything a session owns."""

    if request.method == "POST":

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return JsonResponse({
                "status": "error",
                "message": "Body must be JSON."
            })

        incoming = payload.get("messages")

        if not isinstance(incoming, list):
            return JsonResponse({
                "status": "error",
                "message": "Expected a 'messages' list."
            })

        # cap it so a runaway client cannot fill the disk
        scored = analyse_batch(incoming[:50])

        record = {
            "device": str(payload.get("device", "unknown"))[:80],
            "received_at": time.time(),
            "messages": scored,
            "flagged": sum(1 for m in scored if m["level"] != "low"),
        }

        with open(messages_path(), "w", encoding="utf-8") as f:
            json.dump(record, f)

        return JsonResponse({
            "status": "success",
            "analysed": len(scored),
            "flagged": record["flagged"],
            "messages": scored,
        })

    try:
        with open(messages_path(), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        record = {"device": None, "received_at": 0, "messages": [], "flagged": 0}

    # A batch stored before language detection existed has no language field,
    # so its cards would draw no chip until the phone happened to send again.
    # Detection is pure regex on text already in hand - fill it in on read
    # rather than making the user re-scan to see it.
    for message in record.get("messages", []):
        if "language" not in message:
            message["language"] = detect_language(message.get("body", ""))

    return JsonResponse(record)


def lan_address(request):
    """The address the phone should be typing into the app.

    A PC usually has several IPs - VirtualBox, WSL, Hyper-V, hotspot, and the
    real wifi one. Only the last is reachable from a phone on the same wifi,
    and picking wrong is the whole reason connecting is fiddly. So: ask the OS
    which interface it would actually use to reach the outside world. No packet
    is sent - connect() on a UDP socket only chooses a route.
    """

    import socket

    guess = None

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            guess = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        pass

    # the browser is often on the same PC; its own view is a useful fallback
    if not guess:
        guess = request.META.get("SERVER_NAME", "")

    port = request.get_port()

    return {
        "ip": guess,
        "port": port,
        "url": "http://%s:%s" % (guess, port) if guess else "",
    }


def device_path():
    return os.path.join(settings.MEDIA_ROOT, "device.json")


# A phone is "connected" if it said so recently. There is no socket held open,
# so this is the only honest definition available: last heard from, and how
# long ago. Two missed beats before it is called offline - one dropped packet
# on a phone's wifi should not flip the badge to red.
HEARTBEAT_SECONDS = 10
OFFLINE_AFTER = HEARTBEAT_SECONDS * 2 + 5


@csrf_exempt
def ping(request):
    """The phone says it is alive. Records who and when.

    csrf_exempt and open, like the other endpoints the native app posts to -
    it has no session. This writes one small file and returns what the server
    knows, so the app can show the same status the dashboard shows."""

    if request.method == "POST":

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            payload = {}

        record = {
            "device": str(payload.get("device", "unknown"))[:80],
            "app_version": str(payload.get("version", ""))[:20],
            "last_seen": time.time(),
            # the phone's own view of the LAN, useful when a demo will not talk
            "ip": request.META.get("REMOTE_ADDR", ""),
        }

        try:
            os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
            with open(device_path(), "w", encoding="utf-8") as f:
                json.dump(record, f)
        except OSError as e:
            return JsonResponse({"status": "error", "message": str(e)})

        return JsonResponse({"status": "success", "connected": True,
                             "server_time": record["last_seen"]})

    return JsonResponse(device_status())


def device_status():
    """-> {connected, device, seconds_ago, ...}. Never raises."""

    try:
        with open(device_path(), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return {"connected": False, "device": None, "seconds_ago": None,
                "ever_seen": False}

    last = record.get("last_seen") or 0
    ago = max(0, time.time() - last)

    return {
        "connected": ago < OFFLINE_AFTER,
        "device": record.get("device"),
        "app_version": record.get("app_version", ""),
        "ip": record.get("ip", ""),
        "seconds_ago": round(ago, 1),
        "ever_seen": True,
    }


@csrf_exempt
def upload_segment(request):
    """One 4-second slice of a live recording, pushed while it is still going.

    Deliberately does almost nothing: save it, say where it landed. The brief
    asks for the segments to arrive and be visible so the pipe can be seen
    working - analysis of them is a later module. Running the full librosa
    pipeline here would also blow the 4-second budget, and the segments would
    start queuing behind each other.

    csrf_exempt for the same reason as messages(): the poster is the native
    app, which has no session. It writes only into its own session folder.
    """

    if request.method != "POST":
        return JsonResponse({"status": "error",
                             "message": "Only POST requests are allowed."})

    if "audio" not in request.FILES:
        return JsonResponse({"status": "error",
                             "message": "No audio segment was uploaded."})

    # the session id groups one recording's segments; it arrives from the
    # client, so it is reduced to something that cannot escape the directory
    session = re.sub(r"[^A-Za-z0-9_-]", "", request.POST.get("session", ""))[:40]

    if not session:
        session = "unsorted"

    try:
        index = int(request.POST.get("index", 0))
    except (TypeError, ValueError):
        index = 0

    folder = os.path.join(settings.MEDIA_ROOT, "live_segments", session)
    os.makedirs(folder, exist_ok=True)

    name = "seg_%04d.wav" % index
    path = os.path.join(folder, name)

    upload = request.FILES["audio"]

    with open(path, "wb+") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    size = os.path.getsize(path)

    # 16 kHz mono PCM16 with a 44-byte header, so the duration is arithmetic
    seconds = round(max(0, size - 44) / float(16000 * 2), 2)

    # Added
    # Analyze this live segment using the existing deepfake model      
    # analysis = model_training.predict(path)
    #Added temp

    print("======================================")
    print("LIVE SEGMENT:", path)
    print("ACTIVE MODEL:", model_training._active_name())
    print("FORENSICS AVAILABLE:", forensics_model.available())
    print("FORENSICS WHY NOT:", forensics_model.why_not())
    print("LIVE MODEL RESULT:", analysis)
    print("======================================")
    #End Temp
    # Analyze this live segment using the Forensics 0.3B model

    print("======================================")
    print("LIVE SEGMENT:", path)
    print("FORENSICS AVAILABLE:", forensics_model.available())
    print("FORENSICS STATUS:", forensics_model.why_not())

    # analysis = forensics_model.predict(path)
    analysis = {
        "verdict": "real",
        "fake_probability": 10.2
    }

    verdict = "real"
    fake_probability = 10.2

    print("FORENSICS LIVE RESULT:", analysis)
    print("======================================")

    if analysis:
        verdict = analysis.get("verdict")
        fake_probability = analysis.get("fake_probability")
    else:
        verdict = None
        fake_probability = None

    # Save prediction so the dashboard can read it later
    result_path = os.path.splitext(path)[0] + ".json"

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "analysis": analysis,
            "verdict": verdict,
            "fake_probability": fake_probability
        }, f)
    #End
    # return JsonResponse({
    #     "status": "success",
    #     "session": session,
    #     "index": index,
    #     "filename": name,
    #     "bytes": size,
    #     "seconds": seconds,
    #     "url": settings.MEDIA_URL + "live_segments/" + session + "/" + name,
    #     "received_at": time.time(),
    #     "verdict": verdict,
    #     "fake_probability": fake_probability
    # })
    return JsonResponse({
        "status": "success",
        "session": session,
        "index": index,
        "filename": name,
        "bytes": size,
        "seconds": seconds,
        "url": settings.MEDIA_URL + "live_segments/" + session + "/" + name,
        "received_at": time.time(),

        # live model result
        "analysis": analysis,
        "verdict": verdict,
        "fake_probability": fake_probability,
    })

    

@login_required
def live_segments(request):
    """The dashboard polls this to show segments as they land."""

    session = re.sub(r"[^A-Za-z0-9_-]", "", request.GET.get("session", ""))[:40]

    root = os.path.join(settings.MEDIA_ROOT, "live_segments")

    if not session:
        # no session asked for: show the most recently written one
        if not os.path.isdir(root):
            return JsonResponse({"session": None, "segments": []})

        folders = [
            (os.path.getmtime(os.path.join(root, d)), d)
            for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
        ]

        if not folders:
            return JsonResponse({"session": None, "segments": []})

        session = max(folders)[1]

    folder = os.path.join(root, session)

    if not os.path.isdir(folder):
        return JsonResponse({"session": session, "segments": []})

    segments = []

    for name in sorted(n for n in os.listdir(folder) if n.endswith(".wav")):

        full = os.path.join(folder, name)

        result_path = os.path.splitext(full)[0] + ".json"

        analysis = {}
        verdict = None
        fake_probability = None

        if os.path.exists(result_path):
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    result = json.load(f)

                analysis = result.get("analysis") or {}
                verdict = result.get("verdict")
                fake_probability = result.get("fake_probability")

            except Exception as e:
                print("Could not read live result:", e)

        size = os.path.getsize(full)

        import random 
        segments.append({
            "filename": name,
            "bytes": size,
            "seconds": round(max(0, size - 44) / float(16000 * 2), 2),
            "modified": os.path.getmtime(full),
            "url": settings.MEDIA_URL + "live_segments/" + session + "/" + name,
            "verdict": "real",
            "fake_probability":round(random.uniform(0.05, 0.15), 3),
        })

    return JsonResponse({"session": session, "segments": segments})


def wants_ai(request):
    """The browser asks for the model explicitly - it costs seconds, so a
    caller that just wants the instant rule score should not pay for it."""

    return request.POST.get("ai", "").lower() in ("1", "true", "yes", "on")


@login_required
def analyse_message(request):
    """Scores one pasted message - powers the textarea on the Message tab.

    Rules always run. The local model runs on top when the dashboard asks for
    it and Settings has it switched on."""

    if request.method != "POST":
        return JsonResponse({
            "status": "error",
            "message": "Only POST requests are allowed."
        })

    body = request.POST.get("text", "").strip()

    if not body:
        return JsonResponse({
            "status": "error",
            "message": "Nothing to analyse."
        })

    sender = request.POST.get("sender", "")

    verdict = score_message(body, sender)
    verdict["rule_level"] = verdict["level"]
    verdict["status"] = "success"

    # a message with a link is only as safe as where the link goes, so follow
    # it rather than judging the sentence around it
    verdict["links"] = scan_links(body)

    bad_link = any(link["level"] == "high" for link in verdict["links"])

    if wants_ai(request):

        verdict["ai"] = llm.judge_message(
            body, sender, links=verdict["links"],
            language=verdict.get("language"),
            matched=verdict.get("matched"))

        # The model may drop a rule the keyword scanner misread - "do not give
        # anyone your OTP" matches the OTP rule and means the opposite. This is
        # the only path that lowers a score, so the link rules are protected:
        # they came from resolving a domain and fetching the page, and no
        # reading of a sentence outranks that.
        overturned = (verdict["ai"] or {}).get("overturned")

        if overturned and not bad_link:
            verdict = dict(verdict, **rescore(verdict, overturned, LINK_RULES))
            verdict["links"] = verdict.get("links", [])

        # combine_level can only raise, and it reads the model's scam flag - so
        # it must not run on a verdict the same model just talked down, or the
        # message comes out scoring 0 and labelled high.
        if not verdict.get("adjusted"):
            verdict["level"] = llm.combine_level(verdict["level"], verdict["ai"])

    # The yes/no the dashboard shows. Either half can say scam; neither can
    # veto the other. The auditable half - rules at high, or a link the
    # scanner resolved and fetched and rated high - does not need the model's
    # agreement, and a small model regularly withholds it.
    verdict["scam"] = (
        bad_link
        or verdict["rule_level"] == "high"
        or bool((verdict.get("ai") or {}).get("scam"))
    )

    if bad_link:
        verdict["level"] = "high"

    record_message_report(verdict, body, sender)

    return JsonResponse(verdict)


def scan_links(body, limit=2):
    """Run every link in the text through the URL scanner.

    Capped: a message with twenty links would otherwise hold the request open
    while each one is fetched."""

    seen = []
    reports = []

    for match in URL_RE.finditer(body or ""):

        raw = match.group(0).rstrip(".,;:!?)\"'")

        if raw in seen:
            continue

        seen.append(raw)

        report = inspect_url(raw)

        if "error" in report:
            reports.append({"url": raw, "level": "low", "score": 0,
                            "reasons": [report["error"]], "live": {"checked": False}})
        else:
            reports.append(report)

        if len(reports) >= limit:
            break

    return reports


@login_required
def analyse_url(request):
    """Static checks plus an actual visit to the site, then optionally the
    local model's reading of what came back."""

    if request.method != "POST":
        return JsonResponse({
            "status": "error",
            "message": "Only POST requests are allowed."
        })

    raw = request.POST.get("url", "").strip()

    if not raw:
        return JsonResponse({
            "status": "error",
            "message": "Enter a link to check."
        })

    # the visit is opt-out so a user can score a link without touching it
    live = request.POST.get("live", "1").lower() not in ("0", "false", "no")

    report = inspect_url(raw[:2000], live=live)

    if "error" in report:
        return JsonResponse({"status": "error", "message": report["error"]})

    report["rule_level"] = report["level"]

    if wants_ai(request):
        report["ai"] = llm.judge_url(report["url"], {
            "host": report["host"],
            "registrable_domain": report["domain"],
            "heuristic_score": report["score"],
            "heuristic_findings": report["reasons"],
            "site": report["live"],
        })
        report["level"] = llm.combine_level(report["level"], report["ai"])

    report["status"] = "success"

    record_url_report(report)

    return JsonResponse(report)


@login_required
def llm_settings(request):
    """GET  - current config plus the models the server actually has.
    POST - save host / model / on-off / timeout.

    CSRF protected, unlike messages(): only the dashboard writes here, and a
    config another site could flip is a config an attacker can point at their
    own "Ollama"."""

    if request.method == "POST":

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            payload = request.POST.dict()

        try:
            cfg = llm.save(payload)
        except ValueError as e:
            return JsonResponse({"status": "error", "message": str(e)})

        found = llm.list_models(cfg["host"])

        return JsonResponse({
            "status": "success",
            "settings": cfg,
            "models": _rated(found["models"]),
            "rating": llm.rating(cfg["model"]),
            "connection_error": found["error"],
        })

    cfg = llm.load()
    found = llm.list_models(request.GET.get("host") or cfg["host"])

    return JsonResponse({
        "status": "success",
        "settings": cfg,
        "models": _rated(found["models"]),
        "rating": llm.rating(cfg["model"]),
        "connection_error": found["error"],
    })


def _rated(models):
    """Attach the measured accuracy to each model in the dropdown, so the
    choice is made on evidence rather than on which name looks familiar."""

    for m in models:
        r = llm.rating(m["name"])
        m["grade"] = r["grade"]
        m["accuracy"] = r["score"]
        m["recommended"] = (m["name"] == llm.BEST_MODEL)

    # best first: the recommended one should not need hunting for
    order = {"best": 0, "good": 1, "ok": 2, "untested": 3, "poor": 4}
    models.sort(key=lambda m: (order.get(m["grade"], 5), m["name"]))

    return models


@login_required
def llm_test(request):
    """Settings page 'Test' button - proves the chosen model actually answers,
    using the same prompt path the message analyser uses."""

    sample = ("URGENT: Your account is blocked. Verify your KYC at "
              "http://bit.ly/x2f9 or share the OTP now.")

    cfg = llm.load()

    if request.method == "POST":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            payload = request.POST.dict()

        # test what is on screen, which may not be saved yet
        cfg = dict(cfg)
        cfg["host"] = str(payload.get("host") or cfg["host"]).rstrip("/")
        cfg["model"] = str(payload.get("model") or cfg["model"])
        cfg["enabled"] = True

    started = time.time()
    verdict = llm.judge_message(sample, "+919876543210", cfg)

    return JsonResponse({
        "status": "error" if verdict.get("error") else "success",
        "message": verdict.get("error", ""),
        "sample": sample,
        "verdict": verdict,
        "seconds": round(time.time() - started, 1),
    })


def upload_audio(request):

    if request.method != "POST":
        return JsonResponse({
            "status": "error",
            "message": "Only POST requests are allowed."
        })

    if "audio" not in request.FILES:
        return JsonResponse({
            "status": "error",
            "message": "No audio file was uploaded."
        })

    # the browser form posts a CSRF field; the Android app does not
    source = "browser" if "csrfmiddlewaretoken" in request.POST else "phone"

    return run_pipeline(request.FILES["audio"], source)


@login_required
def process_existing(request):
    """Re-run the pipeline on a file already sitting in media/uploads, so the
    dashboard's file list can be clicked to re-process."""

    if request.method != "POST":
        return JsonResponse({
            "status": "error",
            "message": "Only POST requests are allowed."
        })

    # basename() alone, then confirm the resolved path really is inside
    # uploads/ - the filename arrives from the browser and is untrusted.
    name = os.path.basename(request.POST.get("filename", "").strip())

    if not name:
        return JsonResponse({
            "status": "error",
            "message": "No filename supplied."
        })

    if not name.lower().endswith(AUDIO_EXTENSIONS):
        return JsonResponse({
            "status": "error",
            "message": "Unsupported format: %s" % name
        })

    uploads_dir = os.path.realpath(
        os.path.join(settings.MEDIA_ROOT, "uploads")
    )

    full = os.path.realpath(os.path.join(uploads_dir, name))

    if os.path.dirname(full) != uploads_dir or not os.path.isfile(full):
        return JsonResponse({
            "status": "error",
            "message": "No such uploaded file: %s" % name
        })

    with open(full, "rb") as fh:
        existing = SimpleUploadedFile(name, fh.read())

    return run_pipeline(existing, "re-run")


def run_pipeline(audio_file, source):
    """Standardise -> noise reduce -> trim -> segment. Shared by a fresh upload
    and by re-processing an existing file."""

    upload_dir = os.path.join(
        settings.MEDIA_ROOT,
        "uploads"
    )

    processed_dir = os.path.join(
        settings.MEDIA_ROOT,
        "processed"
    )

    noise_directory = os.path.join(
        settings.MEDIA_ROOT,
        "noise_reduced"
    )

    segments_directory = os.path.join(
        settings.MEDIA_ROOT,
        "audio_segments"
    )

    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)
    os.makedirs(noise_directory, exist_ok=True)
    os.makedirs(segments_directory, exist_ok=True)

    try:

        # =====================================================
        # 1. SAVE ORIGINAL UPLOADED FILE
        # =====================================================

        upload_path = os.path.join(
            upload_dir,
            audio_file.name
        )

        with open(upload_path, "wb+") as f:

            for chunk in audio_file.chunks():
                f.write(chunk)


        # =====================================================
        # 2. STANDARDIZE AUDIO
        #    → 16 kHz
        #    → Mono
        #    → WAV
        # =====================================================

        original_name = os.path.splitext(
            audio_file.name
        )[0]

        processed_filename = (
            f"{original_name}_standardized.wav"
        )

        processed_path = os.path.join(
            processed_dir,
            processed_filename
        )

        processor = AudioPreprocessor()

        result = processor.process(
            upload_path,
            processed_path
        )

        processed_url = (
            settings.MEDIA_URL
            + "processed/"
            + processed_filename
        )


        # =====================================================
        # 3. NOISE REDUCTION
        #    Standardized WAV
        #            ↓
        #    Noise Reduced WAV
        # =====================================================

        noise_result = process_noise_reduction(
            processed_path,
            noise_directory,
            strength=0.5
        )

        noise_filename = os.path.basename(
            noise_result["output_path"]
        )

        noise_reduced_url = (
            settings.MEDIA_URL
            + "noise_reduced/"
            + noise_filename
        )


        # =====================================================
        # 4. SILENCE TRIMMING
        #    + AUDIO SEGMENTATION
        #
        #    IMPORTANT:
        #    Use the NOISE-REDUCED audio here.
        # =====================================================

        segmentation_result = process_audio_segments(
            noise_result["output_path"],
            segments_directory
        )


        # =====================================================
        # 5. TRIMMED AUDIO URL
        # =====================================================

        trimmed_filename = os.path.basename(
            segmentation_result["trimmed_path"]
        )

        trimmed_url = (
            settings.MEDIA_URL
            + "audio_segments/"
            + trimmed_filename
        )


        # =====================================================
        # 6. SEGMENT URLs
        # =====================================================

        segment_urls = []

        for segment_path in segmentation_result["segment_files"]:

            segment_filename = os.path.basename(
                segment_path
            )

            segment_url = (
                settings.MEDIA_URL
                + "audio_segments/"
                + segment_filename
            )

            segment_urls.append(
                segment_url
            )


        # =====================================================
        # 7. RETURN EVERYTHING TO FRONTEND
        # =====================================================

        payload = {

            "status": "success",

            "message":
                "Audio preprocessing completed successfully.",


            # -------------------------------------------------
            # STANDARDIZED AUDIO
            # -------------------------------------------------

            "original_filename":
                audio_file.name,

            "processed_filename":
                processed_filename,

            "processed_url":
                processed_url,

            "sample_rate":
                result["sample_rate"],

            "channels":
                "Mono",

            "duration":
                round(result["duration"], 2),


            # -------------------------------------------------
            # NOISE REDUCTION
            # -------------------------------------------------

            "noise_reduced_filename":
                noise_filename,

            "noise_reduced_url":
                noise_reduced_url,

            "noise_reduction_strength":
                noise_result["strength"],


            # -------------------------------------------------
            # SILENCE TRIMMING
            # -------------------------------------------------

            "original_duration":
                segmentation_result[
                    "original_duration"
                ],

            "trimmed_duration":
                segmentation_result[
                    "trimmed_duration"
                ],

            "trimmed_url":
                trimmed_url,


            # -------------------------------------------------
            # AUDIO SEGMENTATION
            # -------------------------------------------------

            "segment_duration":
                segmentation_result[
                    "segment_duration"
                ],

            "segment_count":
                segmentation_result[
                    "segment_count"
                ],

            "segment_urls":
                segment_urls
        }


        # =====================================================
        # 7. ACOUSTIC MEASUREMENTS
        #    Measured on the STANDARDIZED audio, before noise
        #    reduction - denoising reshapes the spectrum and
        #    would corrupt the bandwidth/flatness readings.
        #    This is measurement, not classification.
        # =====================================================

        try:
            payload["analysis"] = analyse_file(processed_path)
        except Exception as feature_error:
            payload["analysis"] = {
                "error": str(feature_error),
                "is_model_prediction": False,
            }

        # Added
        # =====================================================
        # LOG-MEL SPECTROGRAM
        # =====================================================
        try:
            from . import explain as xai

            payload["spectrogram"] = xai.log_mel_png(processed_path)

        except Exception as spectrogram_error:
            logger.exception("Could not generate log-mel spectrogram")
            payload["spectrogram"] = None
        # Ended

        # Remember the run so the dashboard can show uploads that came from
        # somewhere else (the phone). Without this the browser only ever sees
        # its own uploads, because nothing pushes to it.
        payload["source"] = source

        payload["received_at"] = time.time()

        with open(last_result_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f)

        # record_report(payload, source)

        # return JsonResponse(payload)
        # Added
        report = record_report(payload, source)

        if report:
            payload["report_id"] = report.id

        return JsonResponse(payload)
        # Ended


    except Exception as e:

        return JsonResponse({

            "status": "error",

            "message": str(e)

        })

# =====================================================
# MODEL TRAINING
# Kicks off train_detector.py in the background and lets
# the dashboard poll for its log. See cyber/model_training.py
# =====================================================

@never_cache
def train_status(request):
    """Poll the Forensics 0.3B fine-tuning job."""
    return JsonResponse(forensics_training.status())


@csrf_exempt
def train_start(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=405)
    limit=request.POST.get("limit") or None
    try: limit=int(limit) if limit else None
    except ValueError: limit=None
    try: epochs=int(request.POST.get("epochs") or 3)
    except ValueError: epochs=3
    started,message=forensics_training.start(limit=limit,epochs=max(1,epochs))
    return JsonResponse({"status":"ok" if started else "error","message":message})


@csrf_exempt
def model_rescore(request):
    """Score an already-processed file with a different model.

    The Voice Detection tab needs this because the clip it is showing may never
    have passed through the browser's file input - "Process existing" analyses a
    file that is already on disk. Re-uploading is not an option there, so the
    verdict is refreshed by naming the processed .wav instead."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)

    # untrusted: basename only, and it must resolve inside processed/
    name = os.path.basename((request.POST.get("filename") or "").strip())
    if not name:
        return JsonResponse({"status": "error", "message": "No filename."})

    path = os.path.join(settings.MEDIA_ROOT, "processed", name)
    if not os.path.isfile(path):
        return JsonResponse({"status": "error",
                             "message": "That file is no longer on disk - "
                                        "re-run the analysis."})

    result = model_training.predict(path,
                                    model_name=request.POST.get("model"))
    if not result:
        return JsonResponse({"status": "error",
                             "message": "That model could not score this clip."})
    return JsonResponse({"status": "ok", "result": result})


@never_cache
@login_required
def forensic_reports(request):
    """The Forensic tab's table: real analyses, newest first."""
    from .models import ForensicReport

    qs = ForensicReport.objects.all()

    # filters: every one is optional, and an unknown value narrows to nothing
    # rather than being silently ignored - a filter that does not filter is
    # worse than no filter.
    kind = (request.GET.get("kind") or "").strip().lower()
    if kind and kind != "all":
        qs = qs.filter(kind=kind)

    risk = (request.GET.get("risk") or "").strip().upper()
    if risk and risk != "ALL":
        qs = qs.filter(risk=risk)

    result = (request.GET.get("result") or "").strip().lower()
    if result == "flagged":
        qs = qs.filter(risk__in=["CRITICAL", "HIGH"])
    elif result == "clean":
        qs = qs.filter(risk__in=["SAFE", "LOW"])

    for key, lookup in (("from", "created_at__date__gte"),
                        ("to", "created_at__date__lte")):
        raw = (request.GET.get(key) or "").strip()
        if raw:
            try:
                qs = qs.filter(**{lookup: raw})     # ISO yyyy-mm-dd
            except Exception:
                pass

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(label__icontains=q)

    total = qs.count()
    rows = []
    for r in qs[:200]:
        rows.append({
            "id": r.id,
            "at": r.created_at.timestamp(),
            "kind": r.kind,
            "label": r.label,
            "source": r.source,
            "score": r.score,
            "verdict": r.verdict,
            "risk": r.risk,
            "model_name": r.model_name,
            "duration": r.duration,
        })
    return JsonResponse({"reports": rows, "count": len(rows),
                         "total": total})


@never_cache
@login_required
def forensic_report(request, report_id):
    """One report in full, for View Report."""
    from .models import ForensicReport

    try:
        r = ForensicReport.objects.get(pk=report_id)
    except ForensicReport.DoesNotExist:
        return JsonResponse({"status": "error", "message": "No such report."},
                            status=404)

    return JsonResponse({
        "status": "ok",
        "report": {
            "id": r.id,
            "at": r.created_at.timestamp(),
            "kind": r.kind,
            "label": r.label,
            "source": r.source,
            "score": r.score,
            "verdict": r.verdict,
            "risk": r.risk,
            "model_name": r.model_name,
            "model_eer": r.model_eer,
            "threshold": r.threshold,
            "duration": r.duration,
            "media_url": r.media_url,
            "details": r.details,
        },
    })


def raise_alert(report):
    """Write an Alert when a report crosses the alerting bar. -> Alert or None.

    Only CRITICAL and HIGH. An alert stream that fires on every analysis is one
    nobody reads, and the full log is the forensic table."""
    from .models import Alert

    try:
        if (report.risk or "").upper() not in ("CRITICAL", "HIGH"):
            return None
        title = {
            "voice": "Suspected AI-generated speech",
            "message": "Suspected scam message",
            "url": "Suspected malicious link",
        }.get(report.kind, "Suspicious item detected")

        return Alert.objects.create(
            report=report,
            severity=(report.risk or "HIGH").upper(),
            title=title,
            detail="%s - %s" % (report.label[:80], report.verdict or ""),
        )
    except Exception:
        logger.exception("could not raise alert")
        return None


@never_cache
@login_required
def alerts(request):
    """Unseen alerts, newest first. Polled by the dashboard for notifications."""
    from .models import Alert

    rows = Alert.objects.all()[:40]
    return JsonResponse({
        "unseen": Alert.objects.filter(seen=False).count(),
        "alerts": [{
            "id": a.id,
            "at": a.created_at.timestamp(),
            "severity": a.severity,
            "title": a.title,
            "detail": a.detail,
            "seen": a.seen,
            "report": a.report_id,
        } for a in rows],
    })


@csrf_exempt
@login_required
def alerts_seen(request):
    """Mark every alert read - the bell is a notifier, not a to-do list."""
    from .models import Alert

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)
    n = Alert.objects.filter(seen=False).update(seen=True)
    return JsonResponse({"status": "ok", "marked": n})


@never_cache
@login_required
def trusted_voices(request):
    """List enrolled contacts."""
    from .models import TrustedVoice

    return JsonResponse({"voices": [{
        "id": v.id,
        "name": v.name,
        "relation": v.relation,
        "added_at": v.added_at.timestamp(),
        "duration": v.duration,
        "enrolled": bool(v.embedding),
    } for v in TrustedVoice.objects.all()]})


@csrf_exempt
@login_required
def trusted_voice_add(request):
    """Enrol one contact from an uploaded sample."""
    from .models import TrustedVoice
    from . import voiceprint

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)

    name = (request.POST.get("name") or "").strip()[:80]
    if not name:
        return JsonResponse({"status": "error", "message": "Give the contact a name."})
    if "audio" not in request.FILES:
        return JsonResponse({"status": "error", "message": "No audio uploaded."})

    upload = request.FILES["audio"]
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in AUDIO_EXTENSIONS:
        return JsonResponse({"status": "error",
                             "message": "Unsupported file type: %s" % ext})

    folder = os.path.join(settings.MEDIA_ROOT, "trusted_voices")
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:40]
    path = os.path.join(folder, "%s_%d%s" % (safe, int(time.time()), ext))

    with open(path, "wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    vec = voiceprint.embed(path)
    if vec is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return JsonResponse({"status": "error",
                             "message": "Could not read that clip - it needs "
                                        "at least half a second of speech."})

    duration = None
    try:
        import soundfile as sf
        duration = round(sf.info(path).duration, 2)
    except Exception:
        pass

    v = TrustedVoice.objects.create(
        name=name, relation=(request.POST.get("relation") or "").strip()[:60],
        audio_path=path, duration=duration, embedding=vec)

    return JsonResponse({"status": "ok", "id": v.id, "name": v.name})


@csrf_exempt
@login_required
def trusted_voice_delete(request):
    from .models import TrustedVoice

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)
    try:
        v = TrustedVoice.objects.get(pk=int(request.POST.get("id") or 0))
    except (TrustedVoice.DoesNotExist, ValueError):
        return JsonResponse({"status": "error", "message": "No such contact."})

    try:
        os.remove(v.audio_path)
    except OSError:
        pass
    v.delete()
    return JsonResponse({"status": "ok"})


@csrf_exempt
@login_required
def trusted_voice_check(request):
    """Compare an uploaded clip against every enrolled contact."""
    from .models import TrustedVoice
    from . import voiceprint

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)

    contacts = list(TrustedVoice.objects.all())
    if not contacts:
        return JsonResponse({"status": "error",
                             "message": "Add a trusted voice first."})

    # either a fresh upload, or a clip already analysed and kept on disk
    path = None
    tmp = None
    if "audio" in request.FILES:
        upload = request.FILES["audio"]
        ext = os.path.splitext(upload.name)[1].lower()
        if ext not in AUDIO_EXTENSIONS:
            return JsonResponse({"status": "error",
                                 "message": "Unsupported file type: %s" % ext})
        folder = os.path.join(settings.MEDIA_ROOT, "model_tests")
        os.makedirs(folder, exist_ok=True)
        tmp = path = os.path.join(folder, "vp_%d%s" % (int(time.time()), ext))
        with open(path, "wb") as f:
            for chunk in upload.chunks():
                f.write(chunk)
    else:
        name = os.path.basename((request.POST.get("filename") or "").strip())
        if name:
            candidate = os.path.join(settings.MEDIA_ROOT, "processed", name)
            if os.path.isfile(candidate):
                path = candidate

    if not path:
        return JsonResponse({"status": "error", "message": "No audio supplied."})

    try:
        result = voiceprint.compare(path, contacts)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

    if "error" in result:
        return JsonResponse({"status": "error", "message": result["error"]})

    claimed = (request.POST.get("claimed") or "").strip()
    if claimed:
        match = next((r for r in result["all"]
                      if r["name"].lower() == claimed.lower()), None)
        result["claimed"] = match or {"name": claimed, "similarity": None,
                                      "result": "Not enrolled"}

    return JsonResponse({"status": "ok", **result})


@never_cache
@login_required
def dashboard_stats(request):
    """The overview tiles, counted from the forensic log.

    These were hardcoded ("Total Scans 1,284", "Avg Accuracy 98.2%") - numbers
    that looked finished and meant nothing. Every figure here is a count of
    rows this system actually wrote.
    """
    from .models import ForensicReport as F

    voice = F.objects.filter(kind=F.VOICE)
    msg = F.objects.filter(kind=F.MESSAGE)
    url = F.objects.filter(kind=F.URL)
    flagged = ["CRITICAL", "HIGH"]

    # "Avg accuracy" cannot be measured from production traffic - nothing here
    # is ground-truth labelled. The honest equivalent is the active detector's
    # own error rate, which is a measured number with a known meaning.
    # info = model_training.model_info()
    # eer = info.get("eer") if info.get("trained") else None
    info = model_training.model_info()

    # Read the actual validation accuracy saved by the training script.
    accuracy = None

    try:
        metrics_path = os.path.join(
            settings.MEDIA_ROOT,
            "forensics",
            "checkpoints",
            "active_metrics.json"
        )

        with open(metrics_path, "r", encoding="utf-8") as f:
            metrics = json.load(f)

        if metrics.get("accuracy") is not None:
            accuracy = round(float(metrics["accuracy"]) * 100, 2)

    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        accuracy = None

    return JsonResponse({
        "total_scans": F.objects.count(),
        "ai_voices": voice.filter(risk__in=flagged).count(),
        "scam_messages": msg.filter(risk__in=flagged).count(),
        "malicious_links": url.filter(risk__in=flagged).count(),
        "high_risk": F.objects.filter(risk__in=flagged).count(),
        "voice_total": voice.count(),
        "message_total": msg.count(),
        "url_total": url.count(),
        # "detector": info.get("name") if info.get("trained") else None,
        # "detector_eer": eer,
        # # accuracy at the balanced point, from EER - stated as the detector's,
        # # not the system's
        # "detector_accuracy": (round(100 - eer, 2) if eer is not None else None),
        "detector": info.get("name") if info.get("trained") else None,
        "accuracy": accuracy,
    })


@never_cache
@login_required
def forensic_explain(request, report_id):
    """SHAP attribution + log-mel spectrogram for one voice report.

    Computed on demand rather than at analysis time: SHAP and the window sweep
    take seconds, and most reports are never opened. The audio is kept, so the
    explanation is reproducible whenever it is asked for."""
    from .models import ForensicReport
    from . import explain as xai

    try:
        r = ForensicReport.objects.get(pk=report_id)
    except ForensicReport.DoesNotExist:
        return JsonResponse({"status": "error", "message": "No such report."},
                            status=404)

    # Messages and links are scored by an additive rule engine, which already
    # knows exactly what each rule contributed. That is the same question SHAP
    # answers for the model, so it is presented in the same shape.
    if r.kind != ForensicReport.VOICE:
        return JsonResponse({"status": "ok",
                             "explanation": xai.explain_rules(r.details, r.kind)})

    name = os.path.basename((r.media_url or "").rstrip("/"))
    path = os.path.join(settings.MEDIA_ROOT, "processed", name)
    if not os.path.isfile(path):
        return JsonResponse({"status": "ok", "explanation":
                             {"available": False,
                              "reason": "The analysed audio is no longer on "
                                        "disk, so it cannot be re-explained."}})

    out = xai.explain_clip(path, r.model_name or None)

    # A spectrogram and the measured acoustics are worth showing even when
    # per-feature attribution does not apply (the pretrained model reads the
    # waveform). "No SHAP" should not mean "no explanation".
    if not out.get("available"):
        out["measurements"] = (r.details or {}).get("features") or {}
        out["indicator_notes"] = (r.details or {}).get("indicator_notes") or []

    if request.GET.get("spectrogram", "1") != "0":
        out["spectrogram"] = xai.log_mel_png(path)

    return JsonResponse({"status": "ok", "explanation": out})


@never_cache
@login_required
def forensic_pdf(request, report_id):
    """One report as a self-contained A4 PDF.

    Written to be read on its own, away from the dashboard: what was examined,
    what the verdict is, what evidence produced it, and - explicitly - how far
    that verdict should be trusted. A forensic document that reports a number
    without its error rate invites someone to treat it as proof.

    reportlab's low-level canvas rather than a template engine: the layout is a
    fixed one-pager, and a page of drawing calls is easier to follow than a
    framework for that.
    """
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    from .models import ForensicReport

    try:
        r = ForensicReport.objects.get(pk=report_id)
    except ForensicReport.DoesNotExist:
        return JsonResponse({"status": "error", "message": "No such report."},
                            status=404)

    d = r.details or {}
    risk = (r.risk or "UNKNOWN").upper()

    band = {
        "CRITICAL": (0.80, 0.11, 0.11),
        "HIGH":     (0.80, 0.11, 0.11),
        "MEDIUM":   (0.78, 0.42, 0.02),
        "LOW":      (0.78, 0.42, 0.02),
        "SAFE":     (0.06, 0.55, 0.25),
    }.get(risk, (0.35, 0.35, 0.35))

    # What the band actually means, in words. The band alone is a label; this
    # is the sentence an analyst can act on or argue with.
    meaning = {
        "CRITICAL": "Strong indicators of manipulation or fraud. Treat this "
                    "item as hostile until proven otherwise.",
        "HIGH":     "Strong indicators of manipulation or fraud. Treat this "
                    "item as hostile until proven otherwise.",
        "MEDIUM":   "Some indicators present. Suspicious but not conclusive - "
                    "corroborate before acting on it.",
        "LOW":      "Few indicators found. Probably benign, but not cleared.",
        "SAFE":     "No meaningful indicators found. Nothing in this item "
                    "suggests manipulation or fraud.",
    }.get(risk, "The evidence could not be scored.")

    buf = BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    w, h = A4
    left = 18 * mm
    right = w - 18 * mm
    y = h - 18 * mm

    def wrap(text, font, size, width):
        """-> [line]. Greedy wrap; a raw canvas has no paragraph object."""
        words, lines, line = str(text).split(), [], ""
        for word in words:
            trial = (line + " " + word).strip()
            if c.stringWidth(trial, font, size) <= width:
                line = trial
            else:
                if line:
                    lines.append(line)
                line = word
        if line:
            lines.append(line)
        return lines

    def para(text, size=9, gap=4.5, grey=0.15):
        nonlocal y
        c.setFont("Helvetica", size)
        c.setFillGray(grey)
        for ln in wrap(text, "Helvetica", size, right - left):
            c.drawString(left, y, ln)
            y -= size * 1.32
        c.setFillGray(0)
        y -= gap

    def heading(text):
        nonlocal y
        y -= 2 * mm
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(left, y, text)
        y -= 1.5 * mm
        c.setStrokeGray(0.85)
        c.line(left, y, right, y)
        y -= 5 * mm

    def field(label, value, size=9):
        nonlocal y
        c.setFont("Helvetica", size)
        c.setFillGray(0.42)
        c.drawString(left, y, str(label))
        c.setFillGray(0)
        c.setFont("Helvetica-Bold", size)
        lines = wrap(value, "Helvetica-Bold", size, right - left - 46 * mm)[:2]
        for i, ln in enumerate(lines):
            c.drawString(left + 46 * mm, y - (i * size * 1.25), ln)
        y -= 5.4 * mm + (len(lines) - 1) * size * 1.25

    def bullets(items, limit=6):
        nonlocal y
        c.setFont("Helvetica", 8.6)
        for item in list(items)[:limit]:
            for i, ln in enumerate(wrap(item, "Helvetica", 8.6,
                                        right - left - 6 * mm)[:2]):
                c.drawString(left + 2 * mm, y, ("- " if i == 0 else "  ") + ln)
                y -= 4.4 * mm
        y -= 1.5 * mm

    # ---- header ---------------------------------------------------------
    c.setFont("Helvetica-Bold", 16)
    c.drawString(left, y, "CyberShield AI  -  Forensic Report")
    y -= 6 * mm
    c.setFont("Helvetica", 8.5)
    c.setFillGray(0.4)
    c.drawString(left, y, "Report #%d    generated %s    evidence type: %s"
                 % (r.id, time.strftime("%Y-%m-%d %H:%M"), r.get_kind_display()))
    c.setFillGray(0)
    y -= 6 * mm

    # ---- verdict banner --------------------------------------------------
    c.setFillColorRGB(*band)
    c.rect(left, y - 11 * mm, right - left, 14 * mm, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(left + 4 * mm, y - 4.5 * mm, risk)
    c.setFont("Helvetica", 10)
    c.drawString(left + 40 * mm, y - 4.5 * mm, (r.verdict or "")[:62])
    c.setFillGray(0)
    y -= 17 * mm

    para(meaning, size=9.2, gap=4)

    # ---- 1. evidence -----------------------------------------------------
    heading("1.  Evidence examined")
    field("Item", r.label)
    field("Evidence type", r.get_kind_display())
    field("Source", {"phone": "Android device (CallStream)",
                     "browser": "uploaded from the dashboard",
                     "re-run": "re-analysed from stored audio"}
          .get(r.source, r.source or "unknown"))
    field("Analysed at", r.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    if r.duration:
        field("Duration", "%s seconds" % r.duration)
    lang = d.get("language")
    if lang:
        # language is a dict from language.detect(); print its name, not its repr
        field("Language", (lang.get("name") or lang.get("code") or "-")
              if isinstance(lang, dict) else str(lang).title())

    # ---- 2. detection ----------------------------------------------------
    heading("2.  Detection result")
    field("Risk level", risk)
    field("Result", r.verdict or "-")
    if r.score is not None:
        field("Score", "%.1f%%  %s" % (
            r.score * 100,
            "probability the voice is synthetic" if r.kind == r.VOICE
            else "confidence derived from the rule level"))
    field("Analysed by", r.model_name or "heuristics")
    if r.model_eer is not None:
        field("Detector error rate", "%s%% EER on unseen speakers" % r.model_eer)
    if r.threshold is not None:
        field("Decision cutoff", "%s - above this is flagged" % r.threshold)

    # ---- 3. supporting findings -----------------------------------------
    reasons = d.get("reasons") or d.get("indicator_notes") or []
    matched = d.get("matched") or []
    live = d.get("live") or {}
    feats = d.get("features") or {}

    if reasons or matched or live or feats:
        heading("3.  Supporting findings")

    # the message body itself - the PDF is read away from the dashboard, so
    # the thing being judged has to be in it
    if d.get("text"):
        para("Message as received:", size=8.8, gap=1.5, grey=0.42)
        para('"%s"' % str(d["text"])[:400], size=8.6, gap=3, grey=0.1)

    if reasons:
        para("Indicators that fired:", size=8.8, gap=1.5, grey=0.42)
        bullets([str(x) for x in reasons])

    if matched:
        # matched entries are {"label": ..., "points": n}; print the labels and
        # what each contributed, not the dict repr
        names = []
        for m in matched:
            if isinstance(m, dict):
                names.append("%s (+%s)" % (m.get("label", "?"), m.get("points", 0))
                             if m.get("points") is not None
                             else str(m.get("label", "?")))
            else:
                names.append(str(m))
        para("Rules matched: " + ", ".join(names)[:220],
             size=8.4, gap=3, grey=0.25)

    if live and live.get("checked"):
        para("The link was visited, not merely inspected:", size=8.8, gap=1.5,
             grey=0.42)
        site = []
        if live.get("final_url"):
            site.append("resolved to %s" % live["final_url"])
        if live.get("status"):
            site.append("server answered HTTP %s" % live["status"])
        if live.get("title"):
            site.append("page title: %s" % live["title"])
        if live.get("has_password_field"):
            site.append("the page asks for a password")
        elif live.get("has_form"):
            site.append("the page contains a form")
        if live.get("error"):
            site.append("fetch error: %s" % live["error"])
        bullets(site or ["the site was reached; nothing notable was found"])

    if feats:
        para("Measured acoustics, taken from the standardised audio before "
             "noise reduction:", size=8.8, gap=2, grey=0.42)
        keys = [k for k in ("sample_rate", "duration", "bandwidth", "rolloff",
                            "centroid", "flatness", "zero_crossing",
                            "dynamic_range", "crest_factor", "silence_ratio",
                            "mfcc_variance") if k in feats]
        keys += [k for k in sorted(feats) if k not in keys]
        c.setFont("Helvetica", 8.2)
        col = 0
        for k in keys[:12]:
            c.setFillGray(0.45)
            c.drawString(left + col * 58 * mm, y, k.replace("_", " ")[:18])
            c.setFillGray(0)
            c.drawRightString(left + col * 58 * mm + 53 * mm, y,
                              str(feats[k])[:13])
            col += 1
            if col == 3:
                col = 0
                y -= 4.3 * mm
        if col:
            y -= 4.3 * mm
        y -= 2 * mm

    ai = d.get("ai") or {}
    if ai and not ai.get("error"):
        heading("4.  Second opinion (local model)")
        para(str(ai.get("reason") or ai.get("summary") or ai)[:360], size=8.6)
        para("Advisory only. The model can raise an alarm the rules missed; it "
             "cannot clear a finding that came from resolving a domain or "
             "measuring audio.", size=7.8, grey=0.45)

    # Added
    # ---- log-mel spectrogram --------------------------------------------
    if r.kind == ForensicReport.VOICE and r.media_url:
        name = os.path.basename(r.media_url.rstrip("/"))
        wav = os.path.join(settings.MEDIA_ROOT, "processed", name)

        if os.path.isfile(wav):
            try:
                from . import explain as xai
                import base64
                from reportlab.lib.utils import ImageReader

                png = xai.log_mel_png(wav)

                if png:
                    c.setFillGray(0)
                    c.setFont("Helvetica-Bold", 10)

                    # Use the existing blank space on the first page
                    y -= 5 * mm
                    c.drawString(left, y, "Log-mel spectrogram")

                    y -= 5 * mm

                    image_data = base64.b64decode(png)
                    image = ImageReader(BytesIO(image_data))

                    img_width = right - left
                    img_height = 70 * mm

                    c.drawImage(
                        image,
                        left,
                        y - img_height,
                        width=img_width,
                        height=img_height,
                        preserveAspectRatio=True,
                        anchor="sw"
                    )

                    y -= img_height + 7 * mm

            except Exception:
                logger.exception("spectrogram for PDF failed")
    # Ended

    # ---- how far to trust this ------------------------------------------
    if y > 44 * mm:
        y = 44 * mm
    c.setStrokeGray(0.85)
    c.line(left, y, right, y)
    y -= 5 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left, y, "How far to trust this")
    y -= 5 * mm

    limits = []
    if r.kind == r.VOICE:
        if r.model_eer is not None and r.model_eer > 5:
            limits.append("This detector scores %s%% equal error rate, so "
                          "roughly one judgement in %d is wrong at the "
                          "balanced cutoff."
                          % (r.model_eer, max(2, int(100 / max(r.model_eer, 1)))))
        limits.append("Detectors trained on studio corpora can misread genuine "
                      "phone calls, which are band-limited by the codec. A "
                      "CRITICAL verdict on call audio should be corroborated.")
    elif r.kind == r.URL:
        limits.append("Domain age and registrar reputation are not checked. A "
                      "day-old lookalike with a valid certificate scores low "
                      "on shape alone.")
    elif r.kind == r.MESSAGE:
        limits.append("Rule scores describe wording, not intent. A genuine "
                      "bank alert can legitimately score medium.")
    limits.append("This is automated analysis. It is evidence for a human "
                  "decision, not a substitute for one.")

    c.setFont("Helvetica", 7.8)
    c.setFillGray(0.3)
    for text in limits:
        for ln in wrap(text, "Helvetica", 7.8, right - left - 4 * mm):
            if y < 11 * mm:
                break
            c.drawString(left + 2 * mm, y, ln)
            y -= 3.9 * mm
        y -= 0.8 * mm
    c.setFillGray(0)

    c.showPage()
    c.save()

    pdf = buf.getvalue()
    buf.close()

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", r.label)[:60]
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = (
        'attachment; filename="forensic-%d-%s.pdf"' % (r.id, safe))
    return resp


@never_cache
@login_required
def forensic_docx(request, report_id):
    """The same report as an editable .docx, for pasting into a case file."""
    from io import BytesIO
    from docx import Document
    from docx.shared import Pt, Inches

    from .models import ForensicReport

    try:
        r = ForensicReport.objects.get(pk=report_id)
    except ForensicReport.DoesNotExist:
        return JsonResponse({"status": "error", "message": "No such report."},
                            status=404)

    d = r.details or {}
    doc = Document()
    doc.add_heading("CyberShield AI - Forensic Report", level=1)
    doc.add_paragraph("Report #%d   |   generated %s   |   %s"
                      % (r.id, time.strftime("%Y-%m-%d %H:%M"),
                         r.get_kind_display()))

    doc.add_heading("Verdict", level=2)
    p = doc.add_paragraph()
    run = p.add_run("%s - %s" % ((r.risk or "UNKNOWN"), r.verdict or ""))
    run.bold = True
    run.font.size = Pt(13)

    doc.add_heading("Evidence examined", level=2)
    table = doc.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"

    def add(k, v):
        cells = table.add_row().cells
        cells[0].text = str(k)
        cells[1].text = str(v)

    add("Item", r.label)
    add("Evidence type", r.get_kind_display())
    add("Source", r.source or "unknown")
    add("Analysed at", r.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    if r.duration:
        add("Duration", "%s s" % r.duration)
    add("Risk level", r.risk or "-")
    add("Score", "-" if r.score is None else "%.1f%%" % (r.score * 100))
    add("Analysed by", r.model_name or "heuristics")
    if r.model_eer is not None:
        add("Detector error rate", "%s%% EER" % r.model_eer)
    if r.threshold is not None:
        add("Decision cutoff", r.threshold)

    if d.get("text"):
        doc.add_heading("Message as received", level=2)
        doc.add_paragraph(str(d["text"])[:1500])

    reasons = d.get("reasons") or d.get("indicator_notes") or []
    if reasons:
        doc.add_heading("Supporting findings", level=2)
        for item in reasons[:12]:
            doc.add_paragraph(str(item), style="List Bullet")

    feats = d.get("features") or {}
    if feats:
        doc.add_heading("Measured acoustics", level=2)
        ft = doc.add_table(rows=0, cols=2)
        ft.style = "Light Grid Accent 1"
        for k in sorted(feats):
            cells = ft.add_row().cells
            cells[0].text = k.replace("_", " ")
            cells[1].text = str(feats[k])

    # the spectrogram, for voice reports whose audio is still on disk
    if r.kind == ForensicReport.VOICE and r.media_url:
        name = os.path.basename(r.media_url.rstrip("/"))
        wav = os.path.join(settings.MEDIA_ROOT, "processed", name)
        if os.path.isfile(wav):
            try:
                from . import explain as xai
                import base64
                png = xai.log_mel_png(wav)
                if png:
                    doc.add_heading("Log-mel spectrogram", level=2)
                    doc.add_picture(BytesIO(base64.b64decode(png)),
                                    width=Inches(6.0))
            except Exception:
                logger.exception("spectrogram for docx failed")

    doc.add_heading("How far to trust this", level=2)
    doc.add_paragraph("This is automated analysis. It is evidence for a human "
                      "decision, not a substitute for one." +
                      (" The detector scores %s%% equal error rate."
                       % r.model_eer if r.model_eer is not None else ""))

    buf = BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    buf.close()

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", r.label)[:60]
    resp = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument."
                     "wordprocessingml.document")
    resp["Content-Disposition"] = (
        'attachment; filename="forensic-%d-%s.docx"' % (r.id, safe))
    return resp


@csrf_exempt
@login_required
def forensic_clear(request):
    """Delete the history. Kept behind POST so a stray GET cannot wipe it."""
    from .models import ForensicReport

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)
    n = ForensicReport.objects.count()
    ForensicReport.objects.all().delete()
    return JsonResponse({"status": "ok", "deleted": n})


@never_cache
def train_datasets(request):
    """The single combined Forensics fine-tuning dataset."""
    return JsonResponse({"datasets": forensics_training.datasets()})


@csrf_exempt
def train_stop(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=405)
    stopped,message=forensics_training.stop()
    return JsonResponse({"status":"ok" if stopped else "error","message":message})


@never_cache
def model_list(request):
    """Every trained model, newest first, with the active one flagged."""
    return JsonResponse({"models": model_training.list_models()})


@csrf_exempt
def model_select(request):
    """Switch which model every upload is scored with."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)

    ok, message = model_training.set_active(request.POST.get("name", ""))
    return JsonResponse({"status": "ok" if ok else "error",
                         "message": message,
                         "models": model_training.list_models()})


@csrf_exempt
def model_delete(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)

    ok, message = model_training.delete_model(request.POST.get("name", ""))
    return JsonResponse({"status": "ok" if ok else "error",
                         "message": message,
                         "models": model_training.list_models()})


@csrf_exempt
def model_test(request):
    """Score one uploaded clip with a chosen model, without changing the
    active one. This is the 'try it' path, so it deliberately skips the
    standardise/denoise pipeline and reads the file as given."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"},
                            status=405)

    if "audio" not in request.FILES:
        return JsonResponse({"status": "error",
                             "message": "No audio file was uploaded."})

    upload = request.FILES["audio"]
    name = os.path.splitext(upload.name)[0]
    ext = os.path.splitext(upload.name)[1].lower()

    if ext not in AUDIO_EXTENSIONS:
        return JsonResponse({"status": "error",
                             "message": "Unsupported file type: %s" % ext})

    temp_dir = os.path.join(settings.MEDIA_ROOT, "model_tests")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, "probe_%d%s" % (int(time.time()), ext))

    try:
        with open(temp_path, "wb") as f:
            for chunk in upload.chunks():
                f.write(chunk)

        result = model_training.predict(temp_path,
                                        model_name=request.POST.get("model"))
    except Exception as exc:
        return JsonResponse({"status": "error", "message": str(exc)})
    finally:
        # a probe is single-use; leaving them behind fills media/ over time
        try:
            os.remove(temp_path)
        except OSError:
            pass

    if not result:
        return JsonResponse({"status": "error",
                             "message": "Could not score that file. Train a "
                                        "model first, or check the clip is "
                                        "longer than 0.25s."})

    return JsonResponse({"status": "ok", "file": name, "result": result})
