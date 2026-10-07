"""
Which language is this SMS in - English, Hindi or Marathi.

Stdlib only. A language-id library (langdetect, fasttext) is trained on
paragraphs of prose; an SMS is one line, half of it digits and a bank name, and
Hindi/Marathi share a script *and* much of their vocabulary. Off-the-shelf
detectors do badly on exactly this input, so this does the two things that
actually work at SMS length:

  1. read the script    - Devanagari or Latin, from the Unicode block. Exact.
  2. match marker words - the handful of everyday words the two languages do
                          NOT share. This is what separates hi from mr.

Marathi says "आहे / नाही / तुमच्या / करा", Hindi says "है / नहीं / आपका /
करें". Neither borrows the other's. Romanised SMS ("aapla khate band hoil")
is common too, so the same markers are listed in Latin script.

When the script is Devanagari but no marker matched, it says so - "Hindi or
Marathi" - rather than picking one. A guess dressed as an answer is the thing
this project keeps removing.
"""

import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN_LETTER = re.compile(r"[A-Za-z]")

# Words each language uses and the other does not. Kept short on purpose -
# every entry here has to be one *nobody* would write in the other language.
MARKERS = {
    "mr": {
        "devanagari": (
            "आहे", "आहेत", "नाही", "नाहीत", "तुमच्या", "तुमचे", "तुमचा",
            "तुम्ही", "आपल्या", "करा", "केले", "झाले", "मध्ये", "साठी",
            "कृपया करा", "बँक", "खाते", "त्वरित", "ताबडतोब", "पाठवा",
        ),
        "latin": (
            "aahe", "ahet", "aahet", "tumcha", "tumche", "tumchya", "tumhi",
            "aapla", "aaple", "krupaya", "kara", "zale", "khate", "pathva",
            "taabadtob",
        ),
    },
    "hi": {
        "devanagari": (
            "है", "हैं", "नहीं", "आपका", "आपके", "आपकी", "करें", "कीजिए",
            "किया", "गया", "रहा", "जाएगा", "बैंक", "खाता", "तुरंत", "कृपया करें",
            "भेजें", "के लिए",
        ),
        "latin": (
            "aapka", "aapke", "aapki", "kripya", "karein", "kijiye",
            "jayega", "jaayega", "khata", "bhejein", "turant", "hain",
        ),
    },
}

NAMES = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
    "hi_mr": "Hindi or Marathi",
    "unknown": "Unknown",
}


def _count_markers(text, words, devanagari=False):
    """Marker words present, matched whole rather than as substrings.

    `\\b` cannot be used for Devanagari: a word like आपका ends in a combining
    vowel sign (Unicode category Mn), which Python's `\\w` does not count as a
    word character - so `\\b` finds no boundary there and every marker misses.
    The boundary is therefore spelled out as "no Devanagari either side",
    which includes the marks. Latin markers keep the ordinary `\\b`."""

    found = []

    for word in words:
        pattern = (r"(?<![ऀ-ॿ])%s(?![ऀ-ॿ])" if devanagari else r"\b%s\b")

        if re.search(pattern % re.escape(word), text):
            found.append(word)

    return found


def detect(text):
    """-> {code, name, script, markers[]}. Never raises, never guesses blind."""

    body = (text or "").strip()

    if not body:
        return {"code": "unknown", "name": NAMES["unknown"],
                "script": "none", "markers": []}

    # strip URLs and digits first: "http://sbi-kyc.top" is not evidence of
    # English, it is evidence of nothing
    clean = re.sub(r"(https?://|www\.)\S+", " ", body, flags=re.I)
    clean = re.sub(r"\d+", " ", clean)

    deva = len(DEVANAGARI.findall(clean))
    latin = len(LATIN_LETTER.findall(clean))

    lowered = clean.lower()

    if deva and latin:
        script = "mixed"
    elif deva:
        script = "devanagari"
    elif latin:
        script = "latin"
    else:
        return {"code": "unknown", "name": NAMES["unknown"],
                "script": "none", "markers": []}

    if script == "latin":
        mr_hits = _count_markers(lowered, MARKERS["mr"]["latin"])
        hi_hits = _count_markers(lowered, MARKERS["hi"]["latin"])
    else:
        mr_hits = _count_markers(clean, MARKERS["mr"]["devanagari"], True)
        hi_hits = _count_markers(clean, MARKERS["hi"]["devanagari"], True)

        # a mixed message can carry markers in either script
        if script == "mixed":
            mr_hits += _count_markers(lowered, MARKERS["mr"]["latin"])
            hi_hits += _count_markers(lowered, MARKERS["hi"]["latin"])

    if len(mr_hits) > len(hi_hits):
        return {"code": "mr", "name": NAMES["mr"], "script": script,
                "markers": mr_hits[:6]}

    if len(hi_hits) > len(mr_hits):
        return {"code": "hi", "name": NAMES["hi"], "script": script,
                "markers": hi_hits[:6]}

    # tie. Devanagari on the screen is certain; which of the two is not.
    if deva:
        return {"code": "hi_mr", "name": NAMES["hi_mr"], "script": script,
                "markers": (mr_hits + hi_hits)[:6]}

    return {"code": "en", "name": NAMES["en"], "script": script, "markers": []}


def _selftest():

    def code(t):
        return detect(t)["code"]

    # --- English --------------------------------------------------------
    assert code("Your parcel is out for delivery.") == "en"
    assert code("418181 is OTP to complete the transaction of Rs.10000.0") == "en"

    # --- Hindi, Devanagari ----------------------------------------------
    hi = detect("आपका खाता बंद कर दिया जाएगा। तुरंत KYC करें।")
    assert hi["code"] == "hi", hi
    assert hi["script"] == "mixed", hi          # "KYC" is Latin
    assert hi["markers"], hi

    assert code("यह आपका ओटीपी है, किसी को न बताएं") == "hi"

    # --- Marathi, Devanagari --------------------------------------------
    mr = detect("तुमच्या खात्यातून पैसे काढले आहेत. कृपया तपासा.")
    assert mr["code"] == "mr", mr

    assert code("तुमचे खाते बंद होणार आहे, त्वरित करा") == "mr"

    # --- romanised ------------------------------------------------------
    assert code("Aapka khata band ho jayega, turant KYC karein") == "hi"
    assert code("Tumche khate band hoil, krupaya kara") == "mr"

    # --- Devanagari with nothing that separates the two -----------------
    both = detect("बँक खाते")
    assert both["code"] in ("mr", "hi_mr"), both

    undecidable = detect("सूचना")
    assert undecidable["code"] == "hi_mr", undecidable
    assert undecidable["script"] == "devanagari", undecidable

    # --- nothing to go on -----------------------------------------------
    assert code("") == "unknown"
    assert code("12345 6789") == "unknown"

    # a bare URL is not evidence of English
    assert code("http://sbi-kyc-verify.top/login") == "unknown"

    print("language selftest ok")


if __name__ == "__main__":
    _selftest()
