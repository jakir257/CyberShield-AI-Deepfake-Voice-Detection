"""
Local LLM (Ollama) second opinion.

Stdlib only: this is one HTTP POST to localhost, a dependency would be silly.

The rules in scam_detection.py / url_inspection.py stay the authority - they
are explainable and always available. The model adds a reading of the wording
and of the live page that regex cannot do. Every function here returns
{"error": ...} rather than raising, so the dashboard keeps working when Ollama
is off, slow, or missing the configured model.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

DEFAULTS = {
    "enabled": False,
    "host": "http://localhost:11434",
    "model": "",
    "timeout": 60,
}

# qwen / deepseek style models emit a reasoning block before the answer
THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)

# the model is asked for JSON but sometimes wraps it in prose or a code fence
JSON_RE = re.compile(r"\{.*\}", re.S)


# ---------------------------------------------------------------------------
# Measured accuracy, not opinion.
#
# Every model here was run against the same 11 messages: 4 real scams (English,
# Hindi, Marathi), 3 genuine messages that must stay quiet, and 4 negations
# ("do not give me otp", and the same in Devanagari) where the keyword rules
# misfire and only the model can tell.
#
# Two failure modes, and they are not equally bad:
#   missed  - called a real scam safe. Costs the user money.
#   noisy   - called a genuine bank alert a scam. Costs trust, and is what
#             made this whole feature necessary in the first place.
#
# Re-measure with scratchpad/model_bench.py after changing the prompt.
# ---------------------------------------------------------------------------

RATINGS = {
    "fredrezones55/Gemma-4-Uncensored-HauhauCS-Aggressive:e4b": {
        "grade": "best",
        "score": "11/11",
        "note": "Catches every scam, stays quiet on real bank alerts, and is "
                "the only model that reads negation in Hindi and Marathi.",
    },
    "gemma3:latest": {
        "grade": "good",
        "score": "9/11",
        "note": "Solid. Misses Marathi negation, and flags one genuine bank "
                "alert as a scam.",
    },
    "mistral:7b": {
        "grade": "ok",
        "score": "8/11",
        "note": "Reliable on English. Cannot read negation in Devanagari, so "
                "a Hindi or Marathi warning about OTP fraud scores as a scam.",
    },
    "llama3:8b": {
        "grade": "ok",
        "score": "8/11",
        "note": "Handles Devanagari negation, but calls genuine bank OTP "
                "alerts scams - a false alarm on messages you really get.",
    },
    "translategemma:4b": {
        "grade": "ok",
        "score": "8/11",
        "note": "Same profile as mistral: fine in English, misses Devanagari "
                "negation.",
    },
    "qwen3:4b-instruct": {
        "grade": "poor",
        "score": "7/11",
        "note": "Never overturns a misfired rule, so \"do not share your OTP\" "
                "keeps its score. Reads negation as a scam.",
    },
    "llama3.2:latest": {
        "grade": "poor",
        "score": "7/11",
        "note": "Answers \"not a scam\" to everything, including a prize-and-OTP "
                "scam. The rules carry it entirely.",
    },
}

BEST_MODEL = "fredrezones55/Gemma-4-Uncensored-HauhauCS-Aggressive:e4b"


# Models that cannot read negation in Devanagari. Measured, not guessed: for
# each of these, "do not share your OTP" in Hindi or Marathi still scores as a
# scam. A verdict on such a message carries a warning.
WEAK_ON_DEVANAGARI = (
    "mistral:7b",
    "translategemma:4b",
    "qwen3:4b-instruct",
)


def language_caveat(model, language_code):
    """A warning to show beside a verdict, or None when there is nothing to say."""

    if language_code not in ("hi", "mr", "hi_mr"):
        return None

    if model in WEAK_ON_DEVANAGARI:
        return ("This model misreads negation in Hindi and Marathi - a message "
                "warning *against* fraud can be scored as fraud. Switch to %s "
                "in the LLM tab for these languages." % BEST_MODEL.split("/")[-1])

    if model not in RATINGS:
        return ("This model has not been measured on Hindi or Marathi, so "
                "treat the verdict with care.")

    return None


def rating(model):
    """What we measured about this model, or an honest shrug."""

    if not model:
        return None

    if model in RATINGS:
        return dict(RATINGS[model], model=model, best=BEST_MODEL)

    return {
        "grade": "untested",
        "score": "not measured",
        "note": "This model has not been run against the accuracy suite, so "
                "how it handles negation and genuine bank alerts is unknown.",
        "model": model,
        "best": BEST_MODEL,
    }


def media_root():
    # imported lazily so this module can be run on its own (`python llm.py`)
    # to exercise its selftest without booting Django
    from django.conf import settings
    return str(settings.MEDIA_ROOT)


def settings_path():
    return os.path.join(media_root(), "llm_settings.json")


def load():
    """Current config with defaults filled in. Never raises."""

    cfg = dict(DEFAULTS)

    try:
        with open(settings_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return cfg

    if isinstance(saved, dict):
        for key in DEFAULTS:
            if key in saved:
                cfg[key] = saved[key]

    return cfg


def save(incoming):
    """Validate and persist. Returns the stored config, raises ValueError."""

    cfg = load()

    host = str(incoming.get("host", cfg["host"])).strip().rstrip("/")

    # the host is typed by a user and then fetched server side - only ever
    # allow an http(s) origin, never file:// or a crafted path
    parsed = urllib.parse.urlparse(host)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Host must look like http://localhost:11434")

    cfg["host"] = "%s://%s" % (parsed.scheme, parsed.netloc)
    cfg["model"] = str(incoming.get("model", cfg["model"])).strip()[:120]

    enabled = incoming.get("enabled", cfg["enabled"])
    cfg["enabled"] = str(enabled).lower() in ("1", "true", "yes", "on")

    try:
        cfg["timeout"] = max(5, min(300, int(incoming.get("timeout", cfg["timeout"]))))
    except (TypeError, ValueError):
        cfg["timeout"] = DEFAULTS["timeout"]

    # enabling with no model would fail on every later call instead of once here
    if cfg["enabled"] and not cfg["model"]:
        raise ValueError("Pick a model before enabling the assistant.")

    os.makedirs(media_root(), exist_ok=True)

    with open(settings_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    return cfg


def _request(url, payload=None, timeout=10):
    """-> (parsed json, error string). Exactly one of the two is None."""

    data = None
    headers = {}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        return None, "Ollama returned HTTP %s. %s" % (e.code, detail)

    except urllib.error.URLError as e:
        return None, "Cannot reach Ollama at %s (%s)" % (url, e.reason)

    except (TimeoutError, OSError) as e:
        return None, "Ollama connection failed: %s" % e

    except ValueError:
        return None, "Ollama sent a response that was not JSON."


def list_models(host=None, timeout=8):
    """Model names installed on the server, for the settings dropdown."""

    host = (host or load()["host"]).rstrip("/")

    body, error = _request(host + "/api/tags", timeout=timeout)

    if error:
        return {"models": [], "error": error}

    models = []

    for m in (body or {}).get("models", []):
        name = m.get("name") or m.get("model")

        if name:
            details = m.get("details") or {}
            models.append({
                "name": name,
                "size": m.get("size", 0),
                "family": details.get("family", ""),
                "parameters": details.get("parameter_size", ""),
            })

    models.sort(key=lambda m: m["name"])

    return {"models": models, "error": None}


def generate(prompt, system="", cfg=None, json_mode=True):
    """Raw completion. -> {"text": ..., "model": ...} or {"error": ...}."""

    cfg = cfg or load()

    if not cfg["model"]:
        return {"error": "No model configured. Open Settings and pick one."}

    payload = {
        "model": cfg["model"],
        "prompt": prompt,
        "stream": False,
        "think": False,          # ignored by models that do not reason
        "options": {"temperature": 0},
    }

    if system:
        payload["system"] = system

    if json_mode:
        payload["format"] = "json"

    body, error = _request(
        cfg["host"].rstrip("/") + "/api/generate",
        payload,
        timeout=cfg["timeout"],
    )

    if error:
        return {"error": error}

    text = THINK_RE.sub("", (body or {}).get("response", "")).strip()

    if not text:
        return {"error": "Model '%s' returned nothing." % cfg["model"]}

    return {"text": text, "model": cfg["model"]}


def _as_json(text):
    """Models add prose or code fences around the object more often than not."""

    try:
        return json.loads(text)
    except ValueError:
        pass

    match = JSON_RE.search(text or "")

    if match:
        try:
            return json.loads(match.group(0))
        except ValueError:
            pass

    return None


def _clean_verdict(raw, fallback_text):
    """Whatever the model produced -> the shape the dashboard renders.

    Binary on purpose. A percentage off a 3B model is decoration - it reads as
    precision the thing does not have. Scam or not, plus the reasons."""

    if not isinstance(raw, dict):
        return {
            "error": "Model did not return usable JSON.",
            "raw": (fallback_text or "")[:400],
        }

    scam = raw.get("scam")

    if isinstance(scam, str):
        scam = scam.strip().lower() in ("true", "yes", "1", "scam")

    elif not isinstance(scam, bool):
        # the model answered in its own vocabulary instead of the field asked for
        blob = json.dumps(raw, default=str).lower()
        scam = any(w in blob for w in
                   ("\"scam\"", "fraud", "phish", "malicious", "suspicious"))

    flags = raw.get("red_flags") or raw.get("reasons") or raw.get("indicators") or []

    if isinstance(flags, str):
        flags = [flags]

    if not isinstance(flags, list):
        flags = []

    scam = bool(scam)

    # "not a scam, and here are the red flags" is the contradiction small
    # models produce most often. The answer wins; the leftovers go.
    if not scam:
        flags = []

    # rules the model says the keyword scanner misread. Normalised here;
    # scam_detection.rescore() is what decides whether to honour any of them.
    overturned = []

    for item in (raw.get("overturned") or []):

        if isinstance(item, dict):
            label = str(item.get("label", "")).strip()
            why = str(item.get("why") or item.get("reason", "")).strip()
        elif isinstance(item, str):
            label, why = item.strip(), ""
        else:
            continue

        if label:
            overturned.append({"label": label[:200], "why": why[:300]})

    # The other contradiction: calling it a scam while also striking out the
    # rules that caught it. The prompt says an overturn only applies when the
    # answer is "not a scam", so the answer wins and the deductions go. Erring
    # this way keeps the score up, which is the safe direction to be wrong in.
    if scam:
        overturned = []

    return {
        "scam": scam,
        "red_flags": [str(f)[:200] for f in flags][:8],
        "overturned": overturned[:6],
        "explanation": str(raw.get("explanation", ""))[:600],
        "advice": str(raw.get("advice", ""))[:400],
    }


MESSAGE_SYSTEM = (
    "You are a fraud analyst reviewing one SMS for an Indian mobile user. "
    "Decide one thing: is this message a scam, yes or no.\n"
    "\n"
    "The message may be in English, Hindi or Marathi, written in Devanagari "
    "or romanised (\"aapka khata band ho jayega\"), or a mix of both in one "
    "sentence - that mixing is normal in Indian SMS, it is not itself "
    "suspicious. Judge it in whatever language it is written. Always write "
    "your explanation and advice in English: the dashboard is English.\n"
    "\n"
    "Answer YES when the message asks the recipient to DO something that "
    "costs them. Any one of these is enough:\n"
    "- it asks them to reply with, share, forward or enter an OTP, PIN, "
    "password, CVV, card number or account number - a real bank NEVER asks "
    "for these;\n"
    "- it announces a prize, lottery, lucky draw, refund or reward they did "
    "not apply for;\n"
    "- it threatens that an account will be blocked, suspended or closed "
    "unless they act now;\n"
    "- it pushes them to a link to verify, re-KYC, unblock, update or claim;\n"
    "- it tells them to call a number or install an app to receive money.\n"
    "\n"
    "Answer NO when the message only informs and asks for nothing. These are "
    "normal:\n"
    "- a bank OTP or transaction alert saying an amount was debited, credited "
    "or is being transacted - it tells you a code, it does not ask for one;\n"
    "- the words 'do not share this OTP with anyone' - that is the bank's own "
    "safety warning. It is a sign the message is genuine, never a red flag;\n"
    "- a login OTP from a university, employer or service the user uses;\n"
    "- a delivery, ticket, bill or appointment notice;\n"
    "- ordinary personal conversation.\n"
    "\n"
    "An alphanumeric sender id such as VA-UNIONB-T or JK-SBIUPI-S is a "
    "registered business sender - real banks and companies send from these. A "
    "plain mobile number claiming to be a bank is the suspicious case.\n"
    "\n"
    "The scanner section reports what is really at the end of any link in the "
    "message. Trust it over what the message claims about itself.\n"
    "\n"
    "A keyword scanner has already run and its matched rules are listed "
    "below. It matches words, not meaning, so it cannot tell a demand from a "
    "refusal: \"give me your OTP\" and \"do not give anyone your OTP\" match "
    "the same rule. You can read the sentence, so name any listed rule that "
    "misfired, in \"overturned\".\n"
    "\n"
    "Overturn a rule ONLY when the message does the opposite of what the rule "
    "assumes - it warns against the thing, refuses it, quotes someone else "
    "doing it, or reports it after the fact. Never overturn a rule just "
    "because the message seems harmless overall, and never overturn one you "
    "were not shown.\n"
    "\n"
    "These two answers go together. If your explanation says the message "
    "warns against, refuses or quotes what a listed rule describes, then that "
    "rule misfired and MUST appear in \"overturned\" - copy its text exactly "
    "from the list. Saying \"this warns against sharing an OTP\" while "
    "leaving \"overturned\" empty is a contradiction. Only when scam is true, "
    "or no listed rule was misread, is the list empty.\n"
    "\n"
    "Worked example. Rule listed: \"asks for OTP / PIN / password\".\n"
    "  \"send me your OTP\"        -> scam true,  overturned []\n"
    "  \"never share your OTP\"    -> scam false, overturned [{\"label\":"
    "\"asks for OTP / PIN / password\",\"why\":\"warns against sharing an "
    "OTP, it does not request one\"}]\n"
    "\n"
    "Answer with ONLY a JSON object, no prose: "
    '{"scam":true or false,"red_flags":["short phrase, only real ones"],'
    '"overturned":[{"label":"the rule text, copied exactly","why":"how the '
    'message contradicts it"}],"explanation":"one or two sentences saying '
    'why","advice":"what the recipient should do"}. When scam is false, '
    "red_flags must be empty."
)

URL_SYSTEM = (
    "You are a phishing analyst. You get a URL plus the facts a scanner "
    "collected by actually fetching it. Decide one thing: is this link a "
    "scam, yes or no. A well known brand on its own real domain is not; a "
    "lookalike domain, a shortener hiding its destination, an invalid "
    "certificate, or a login form on an unrelated host is. Answer with ONLY a "
    "JSON object, no prose: "
    '{"scam":true or false,"red_flags":["short phrase"],'
    '"explanation":"one or two sentences","advice":"what the user should do"}'
)


def judge_message(text, sender="", cfg=None, links=None, language=None,
                  matched=None):
    """LLM opinion on one message. -> verdict dict or {"error": ...}.

    links: what url_inspection found for each link in the body. A message
    lives or dies by where its link actually goes, and the model cannot follow
    one - so it is handed the scan instead of being left to guess.

    language: what language.detect() decided, passed on so the model is not
    also guessing the language while judging the content."""

    cfg = cfg or load()

    if not cfg["enabled"]:
        return {"error": "Local LLM is switched off."}

    prompt = 'Sender: %s\n' % (sender or "unknown")

    if language and language.get("code") not in (None, "unknown"):
        prompt += "Detected language: %s (%s script)\n" % (
            language.get("name", "?"), language.get("script", "?"))

    prompt += 'Message:\n"""\n%s\n"""' % (text or "")[:4000]

    if links:
        prompt += "\n\nScanner results for the link(s) in this message:\n"
        prompt += json.dumps(links, indent=2, default=str)[:2500]
    else:
        prompt += "\n\nThe message contains no link."

    if matched:
        prompt += "\n\nKeyword rules that matched (overturn only the ones the "
        prompt += "message contradicts):\n"
        prompt += "\n".join("- %s" % r["label"] for r in matched)

    out = generate(prompt, MESSAGE_SYSTEM, cfg)

    if "error" in out:
        return out

    verdict = _clean_verdict(_as_json(out["text"]), out["text"])
    verdict["model"] = out["model"]

    # If this model is known to misread the language of THIS message, say so
    # on the verdict rather than letting a confident wrong answer stand alone.
    caveat = language_caveat(out["model"], (language or {}).get("code"))
    if caveat:
        verdict["caveat"] = caveat

    return verdict


def judge_url(url, facts, cfg=None):
    """LLM opinion on one URL, given the scanner's observations."""

    cfg = cfg or load()

    if not cfg["enabled"]:
        return {"error": "Local LLM is switched off."}

    prompt = "URL: %s\n\nScanner observations:\n%s" % (
        url[:500],
        json.dumps(facts, indent=2, default=str)[:3000],
    )

    out = generate(prompt, URL_SYSTEM, cfg)

    if "error" in out:
        return out

    verdict = _clean_verdict(_as_json(out["text"]), out["text"])
    verdict["model"] = out["model"]

    return verdict


def combine_level(rule_level, ai):
    """Merge the model's yes/no into the rule verdict.

    The model may raise the alarm, never lower it. Rules are the floor because
    they are the half of the system you can audit line by line - a small model
    shrugging at a textbook OTP scam must not talk the score down."""

    if not isinstance(ai, dict) or ai.get("error") or "scam" not in ai:
        return rule_level

    return "high" if ai["scam"] else rule_level


def _selftest():
    """Offline checks: the parsing and normalising, not the network."""

    assert _as_json('{"a": 1}') == {"a": 1}
    assert _as_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert _as_json('Sure! {"a": 3} hope that helps') == {"a": 3}
    assert _as_json("no object here") is None
    assert _as_json("") is None

    assert THINK_RE.sub("", "<think>hmm</think>{}").strip() == "{}"

    good = _clean_verdict(
        {"scam": True, "red_flags": ["asks for OTP"],
         "explanation": "x", "advice": "y"}, "")
    assert good["scam"] is True and good["red_flags"] == ["asks for OTP"], good
    assert "confidence" not in good, good      # binary answer, no fake precision

    assert _clean_verdict({"scam": False}, "")["scam"] is False
    assert _clean_verdict({"scam": "true"}, "")["scam"] is True
    assert _clean_verdict({"scam": "no"}, "")["scam"] is False

    # a model answering in its own vocabulary still lands somewhere sane
    assert _clean_verdict({"verdict": "phishing"}, "")["scam"] is True
    assert _clean_verdict({"verdict": "legitimate"}, "")["scam"] is False

    assert _clean_verdict(None, "garbage")["error"]

    one = _clean_verdict({"scam": True, "red_flags": "just one"}, "")
    assert one["red_flags"] == ["just one"], one

    # overturned rules, in the several shapes models actually emit
    assert _clean_verdict({"scam": False}, "")["overturned"] == []

    turned = _clean_verdict({"scam": False, "overturned": [
        {"label": "asks for OTP / PIN / password", "why": "it refuses"}]}, "")
    assert turned["overturned"] == [
        {"label": "asks for OTP / PIN / password", "why": "it refuses"}], turned

    # a bare string, and "reason" instead of "why"
    loose = _clean_verdict({"scam": False, "overturned": [
        "contains a link", {"label": "money amount mentioned",
                            "reason": "quoting a scam"}]}, "")
    assert loose["overturned"][0] == {"label": "contains a link", "why": ""}
    assert loose["overturned"][1]["why"] == "quoting a scam", loose

    # junk entries are dropped, not trusted
    junk = _clean_verdict({"scam": False, "overturned": [None, 7, {}, ""]}, "")
    assert junk["overturned"] == [], junk

    # "it is a scam, and also strike out the rules that caught it" - the
    # answer wins, so no points come off
    contradictory = _clean_verdict({"scam": True, "overturned": [
        {"label": "asks for OTP / PIN / password", "why": "warns against"}]}, "")
    assert contradictory["overturned"] == [], contradictory

    # a "no" answer must not arrive with red flags attached to it
    contradiction = _clean_verdict(
        {"scam": False, "red_flags": ["PLS DO NOT SHARE WITH ANYONE"]}, "")
    assert contradiction["red_flags"] == [], contradiction

    # the model raises the level, never lowers it
    assert combine_level("low", {"scam": True}) == "high"
    assert combine_level("high", {"scam": False}) == "high"
    assert combine_level("medium", {"scam": False}) == "medium"
    assert combine_level("low", {"scam": False}) == "low"
    assert combine_level("low", {"error": "off"}) == "low"
    assert combine_level("medium", None) == "medium"

    # ---- measured ratings and the warnings they drive ------------------

    assert rating("") is None
    assert rating(BEST_MODEL)["grade"] == "best"
    assert rating(BEST_MODEL)["score"] == "11/11"
    assert rating("mistral:7b")["grade"] == "ok"

    unknown = rating("some-model-nobody-tested:1b")
    assert unknown["grade"] == "untested", unknown
    assert unknown["best"] == BEST_MODEL, unknown

    # English never carries a language warning, whatever the model
    for model in ("mistral:7b", BEST_MODEL, "anything:1b"):
        assert language_caveat(model, "en") is None, model
        assert language_caveat(model, "unknown") is None, model

    # a model measured as weak on Devanagari warns on hi / mr / hi_mr
    for code in ("hi", "mr", "hi_mr"):
        warned = language_caveat("mistral:7b", code)
        assert warned and "misreads negation" in warned, (code, warned)

    # the best model has nothing to apologise for
    assert language_caveat(BEST_MODEL, "hi") is None
    assert language_caveat("llama3:8b", "mr") is None      # handles negation

    # an unmeasured model says so rather than staying quiet
    untested = language_caveat("some-model-nobody-tested:1b", "hi")
    assert untested and "not been measured" in untested, untested

    print("llm selftest ok")


if __name__ == "__main__":
    _selftest()
