"""
Rule-based scam scoring for SMS / short text.

Deliberately NOT a model. Every point added is traceable to a rule you can
read, and the matched reasons are returned alongside the score so the
dashboard can show *why* a message was flagged rather than asserting a
probability nothing computed.
"""

import re

try:
    from .language import detect as detect_language
except ImportError:        # running the file directly for its selftest
    from language import detect as detect_language

# ---------------------------------------------------------------------------
# Signals. Each entry: (points, label, compiled pattern)
# ---------------------------------------------------------------------------

URL_SHORTENERS = (
    "bit.ly", "tinyurl", "goo.gl", "t.co", "ow.ly", "is.gd",
    "buff.ly", "cutt.ly", "rb.gy", "shorturl", "rebrand.ly",
)

RULES = [
    (30, "asks for OTP / PIN / password",
     r"\b(otp|one[\s-]?time[\s-]?password|pin\s*(code|number)?|cvv|"
     r"password|passcode|mpin)\b"
     r"|ओटीपी|पासवर्ड|पिन\s*(कोड|नंबर)?|गुप्त\s*(शब्द|क्रमांक)"),

    (25, "prize or lottery claim",
     r"\b(won|winner|winning|lottery|jackpot|prize|lucky\s+draw|"
     r"congratulation[s]?)\b"
     r"|लॉटरी|इनाम|इनामी|बक्षीस|जिंकल|जीत(ा|े|ी)|विजेता|"
     r"बधाई|अभिनंदन|भाग्यशाली"),

    (25, "KYC / account verification pressure",
     r"\b(kyc|re[\s-]?kyc|verify\s+your\s+(account|identity|details)|"
     r"account\s+(will\s+be\s+)?(suspend|block|clos|deactivat)\w*)\b"
     r"|केवायसी|खात(ा|े)\s*(बंद|ब्लॉक|बॅन)|"
     r"(बंद|ब्लॉक)\s*(हो|कर|केल)\w*|सत्यापित|पडताळणी|पुन्हा\s*सक्रिय"),

    (20, "urgency / deadline pressure",
     r"\b(urgent(ly)?|immediate(ly)?|within\s+\d+\s*(hour|hrs|minute|min)|"
     r"expire[sd]?|last\s+chance|act\s+now|failure\s+to)\b"
     r"|तुरंत|त्वरित|ताबडतोब|तात्काळ|आज\s*ही|शेवटची\s*संधी|"
     r"अंतिम\s*(तारीख|मुदत)|अन्यथा"),

    (20, "instructs you to click a link",
     r"\b(click\s+(here|below|this|the\s+link)|tap\s+here|"
     r"follow\s+the\s+link|open\s+the\s+link)\b"
     r"|लिंक\s*(पर|वर)?\s*(क्लिक|दाबा|उघडा)|यहाँ\s*क्लिक|इथे\s*क्लिक"),

    (15, "refund / cashback bait",
     r"\b(refund|cashback|cash\s?back|reward|bonus|credited|"
     r"claim\s+your)\b"
     r"|रिफंड|परतावा|कॅशबॅक|कैशबैक|बोनस|जमा\s*(झाल|हुआ|किया)"),

    (15, "requests a bank or card detail",
     r"\b(bank\s+account|debit\s+card|credit\s+card|card\s+number|"
     r"ifsc|upi\s*id|net\s?banking)\b"
     r"|ब(ैं|ँ)क\s*(खात|अकाउंट)|डेबिट\s*कार्ड|क्रेडिट\s*कार्ड|"
     r"कार्ड\s*(नंबर|क्रमांक)|यूपीआय|यूपीआई"),

    (10, "asks you to call a number back",
     r"\b(call\s+(us\s+)?(back\s+)?(on|at)?\s*\+?\d[\d\s-]{7,})\b"
     r"|(फोन|कॉल|संपर्क)\s*(करा|करें|कीजिए|साधा)"),

    (10, "money amount mentioned",
     r"(?:rs\.?|inr|usd|eur|gbp|₹|\$|€|£)\s?\d[\d,]*"
     r"|\d[\d,]*\s*(रुपये|रुपए|रु\.?)"),

    (10, "impersonates a bank or authority",
     r"\b(sbi|hdfc|icici|axis|paytm|phonepe|income\s?tax|customs|"
     r"police|court|government|govt)\b"
     r"|भारतीय\s*स्टेट\s*बँक|एसबीआय|एसबीआई|आयकर|पोलीस|पुलिस|"
     r"न्यायालय|अदालत|सरकार(ी|ने)?"),
]

# re.UNICODE is the default in py3; re.I is here for the Latin half only -
# Devanagari has no case, so it costs nothing and changes nothing there.
COMPILED = [(pts, label, re.compile(pat, re.I)) for pts, label, pat in RULES]

URL_RE = re.compile(r"(https?://|www\.)\S+", re.I)


def score_message(text, sender=""):
    """Return {score 0-100, level, reasons[]} for one message."""

    body = (text or "").strip()

    if not body:
        return {"score": 0, "level": "low", "reasons": [], "matched": [],
                "language": detect_language("")}

    reasons = []
    score = 0

    # what each matched rule contributed, so a later pass can overturn one by
    # name and show the arithmetic rather than just producing a new number
    matched = []

    for points, label, pattern in COMPILED:
        if pattern.search(body):
            score += points
            reasons.append(label)
            matched.append({"label": label, "points": points})

    # links are weighted by how suspicious the host looks
    if URL_RE.search(body):
        if any(s in body.lower() for s in URL_SHORTENERS):
            score += 25
            reasons.append("shortened link hides its destination")
            matched.append({"label": "shortened link hides its destination",
                            "points": 25})
        else:
            score += 15
            reasons.append("contains a link")
            matched.append({"label": "contains a link", "points": 15})

    # a real bank uses an alphanumeric sender id, not a mobile number
    if sender and re.fullmatch(r"\+?\d{10,14}", sender.strip()):
        score += 5
        reasons.append("sent from a personal number, not a business sender id")
        matched.append({
            "label": "sent from a personal number, not a business sender id",
            "points": 5})

    # SHOUTING is common in scam blasts. Devanagari is caseless, so this
    # rule simply never fires on it rather than needing a language check.
    letters = [c for c in body if c.isalpha()]
    if len(letters) >= 20:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_ratio > 0.6:
            score += 5
            reasons.append("mostly capital letters")
            matched.append({"label": "mostly capital letters", "points": 5})

    score = min(score, 100)

    if score >= 60:
        level = "high"
    elif score >= 30:
        level = "medium"
    else:
        level = "low"

    return {"score": score, "level": level, "reasons": reasons,
            "matched": matched, "language": detect_language(body)}


def level_for(score):
    if score >= 60:
        return "high"
    return "medium" if score >= 30 else "low"


def rescore(verdict, overturned, protected=()):
    """Subtract the rules a reader of the *context* says were misread.

    "give me otp" and "do not give me otp" both match the OTP rule, because a
    regex sees the word and not the sentence around it. The second one is a
    refusal. Only something that reads context can tell them apart, so the
    model is allowed to name a matched rule and say it misfired.

    This is the one place the model may LOWER a score, so it is fenced:

      * it can only name a rule that actually matched. It cannot invent a
        deduction, and an unrecognised label is ignored, not trusted.
      * `protected` labels can never be overturned. Callers put the link
        findings in here - "this domain does not resolve" came from a DNS
        lookup, not from reading a sentence, and no opinion outranks it.
      * nothing is deleted. The rule stays in the output marked `overturned`
        with the reason, so the arithmetic is on screen.

    Returns a NEW verdict; the original is left alone."""

    out = dict(verdict)
    matched = [dict(m) for m in verdict.get("matched", [])]

    # {label: why} of the rules the reader wants dropped
    wanted = {}

    for item in (overturned or []):
        if isinstance(item, dict):
            label = str(item.get("label", "")).strip()
            why = str(item.get("why", "")).strip()
        else:
            label, why = str(item).strip(), ""

        if label:
            wanted[label] = why

    removed = 0

    for rule in matched:

        if rule["label"] not in wanted or rule["label"] in protected:
            continue

        rule["overturned"] = True
        rule["why"] = wanted[rule["label"]]
        removed += rule["points"]

    out["matched"] = matched
    out["rule_score"] = verdict["score"]
    out["rule_level"] = verdict["level"]
    out["score"] = max(0, verdict["score"] - removed)
    out["level"] = level_for(out["score"])
    out["adjusted"] = removed > 0

    out["reasons"] = [r["label"] for r in matched if not r.get("overturned")]

    return out


def analyse_batch(messages):
    """messages: [{sender, body, date}] -> same list with scoring attached,
    sorted so the most suspicious surface first."""

    out = []

    for m in messages:
        verdict = score_message(m.get("body", ""), m.get("sender", ""))
        out.append({
            "sender": m.get("sender", "unknown"),
            "body": m.get("body", ""),
            "date": m.get("date", 0),
            "score": verdict["score"],
            "level": verdict["level"],
            "reasons": verdict["reasons"],
            "language": verdict["language"],
        })

    out.sort(key=lambda m: (-m["score"], -m["date"]))
    return out


def _selftest():
    """Fails if the rules stop separating obvious scams from ordinary texts."""

    scam = score_message(
        "URGENT: Your SBI account will be suspended. Verify KYC now: "
        "http://bit.ly/xyz or share OTP to avoid blocking.", "+919876543210")
    assert scam["level"] == "high", scam
    assert scam["score"] >= 60, scam
    assert any("OTP" in r for r in scam["reasons"]), scam["reasons"]

    ham = score_message("Hey, are we still on for lunch at 1pm?", "AMANDA")
    assert ham["level"] == "low", ham
    assert ham["score"] == 0, ham

    prize = score_message("Congratulations! You have won Rs.50,000 in our "
                          "lucky draw. Click here to claim your prize.")
    assert prize["level"] == "high", prize

    # an ordinary delivery notice has a link but little else - must not be "high"
    notice = score_message("Your parcel is out for delivery. Track at "
                           "https://couriers.example.com/track/8891")
    assert notice["level"] in ("low", "medium"), notice

    empty = score_message("")
    assert empty["score"] == 0 and empty["reasons"] == []

    batch = analyse_batch([
        {"sender": "MUM", "body": "call me", "date": 2},
        {"sender": "+919876543210",
         "body": "You WON a prize! Share your OTP and click http://bit.ly/x",
         "date": 1},
    ])
    assert batch[0]["level"] == "high", batch
    assert batch[0]["sender"] == "+919876543210", batch
    assert batch[0]["language"]["code"] == "en", batch[0]["language"]

    # ---- the same scams in Hindi and Marathi must score the same way ----

    hindi = score_message(
        "आपका SBI खाता बंद कर दिया जाएगा। तुरंत केवायसी करें और "
        "ओटीपी भेजें।", "+919876543210")
    assert hindi["level"] == "high", hindi
    assert hindi["language"]["code"] == "hi", hindi["language"]
    assert "asks for OTP / PIN / password" in hindi["reasons"], hindi
    assert "KYC / account verification pressure" in hindi["reasons"], hindi
    assert "urgency / deadline pressure" in hindi["reasons"], hindi

    marathi = score_message(
        "अभिनंदन! तुम्ही ५०,००० रुपये लॉटरी जिंकली आहे. बक्षीस मिळवण्यासाठी "
        "त्वरित ओटीपी पाठवा.", "+918888777666")
    assert marathi["level"] == "high", marathi
    assert marathi["language"]["code"] == "mr", marathi["language"]
    assert "prize or lottery claim" in marathi["reasons"], marathi

    # romanised Hindi - the script is Latin but the scam is the same
    roman = score_message(
        "Aapka khata band ho jayega. Turant KYC karein aur OTP bhejein.",
        "+919876543210")
    assert roman["level"] == "high", roman
    assert roman["language"]["code"] == "hi", roman["language"]

    # and an ordinary Marathi message stays clean
    ordinary = score_message("मी उद्या येतो आहे, तुम्ही घरी आहात का?", "AAI")
    assert ordinary["level"] == "low", ordinary
    assert ordinary["score"] == 0, ordinary
    assert ordinary["language"]["code"] == "mr", ordinary["language"]

    # a real Hindi bank alert informs, it does not pressure - not "high"
    alert = score_message(
        "आपके खाते में 1000 रुपये जमा किया गया है। - SBI", "JK-SBIUPI-S")
    assert alert["level"] in ("low", "medium"), alert

    # ---- context override: the one place a score may go DOWN -----------

    # the case from the brief. Both phrasings match the OTP rule; only the
    # second one is a refusal, and only a reader of context can tell.
    demand = score_message("give me otp")
    refusal = score_message("do not give me otp")
    assert demand["score"] == refusal["score"] == 30, (demand, refusal)

    fixed = rescore(refusal, [{"label": "asks for OTP / PIN / password",
                               "why": "declines to share an OTP"}])
    assert fixed["score"] == 0, fixed
    assert fixed["level"] == "low", fixed
    assert fixed["rule_score"] == 30 and fixed["rule_level"] == "medium", fixed
    assert fixed["adjusted"] is True, fixed
    assert fixed["reasons"] == [], fixed
    # the rule is struck through, not deleted - the arithmetic stays visible
    assert fixed["matched"][0]["overturned"] is True, fixed
    assert fixed["matched"][0]["why"] == "declines to share an OTP", fixed

    # untouched when nothing is overturned
    same = rescore(demand, [])
    assert same["score"] == 30 and same["adjusted"] is False, same

    # a label that never matched cannot invent a deduction
    invented = rescore(demand, [{"label": "prize or lottery claim"}])
    assert invented["score"] == 30, invented

    # a protected label survives being named - link findings came from DNS
    # and a fetch, not from reading a sentence
    linked = score_message("Verify now at http://bit.ly/x")
    guarded = rescore(linked,
                      [{"label": "shortened link hides its destination"}],
                      protected=("shortened link hides its destination",))
    assert guarded["score"] == linked["score"], guarded
    assert guarded["adjusted"] is False, guarded

    # a real scam does not get talked down to safe by overturning one rule
    survives = rescore(scam, [{"label": "asks for OTP / PIN / password"}])
    assert survives["level"] == "high", survives

    # the original verdict is never mutated
    assert refusal["score"] == 30, refusal

    assert level_for(0) == "low" and level_for(30) == "medium"
    assert level_for(59) == "medium" and level_for(60) == "high"

    print("scam_detection selftest ok")


if __name__ == "__main__":
    _selftest()
