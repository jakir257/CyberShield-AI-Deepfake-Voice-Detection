# CyberShield AI — Project Explanation

A working record of what this system does, how each part works, and — just as
important — **what it does not do**. Written so someone who has never seen the
code can understand both the engineering and the reasoning.

> **Just want to run it?** `setup.bat` once, then `run.bat`. See
> [SETUP.md](../SETUP.md) for the details, the optional Forensics 0.3B model,
> and troubleshooting.

---

## 1. What the system is

An Android phone captures audio and SMS, sends them to a PC over the local
network, and the PC analyses them. Two separate servers do different jobs.

```
   ANDROID PHONE                        WINDOWS PC
   (CallStream.apk)                     192.168.1.7

   ┌───────────────┐   HTTP upload      ┌──────────────────────────┐
   │ SCAN & DETECT ├───────────────────►│  Django  :8000           │
   │ RECORD        │   multipart POST   │  Audio pipeline          │
   │ SCAN MESSAGES ├───────────────────►│  Scam scoring            │
   └───────┬───────┘   JSON POST        │  URL inspection          │
           │                            │  Dashboard (browser UI)  │
           │                            └────────┬────────┬────────┘
           │                                     │        │
           │                        /api/generate│        │HTTP(S)
           │                                     ▼        ▼
           │                            ┌──────────────┐ ┌──────────────┐
           │                            │ Ollama :11434│ │ the open web │
           │                            │ local LLM    │ │ (link tests) │
           │                            └──────────────┘ └──────────────┘
           │           WebSocket        ┌──────────────────────────┐
           └───────────────────────────►│  FastAPI :8765           │
                       PCM frames       │  Whisper transcription   │
                                        │  Tkinter monitor window  │
                                        └──────────────────────────┘
```

Two servers because they solve different problems. Django is **request/response**
— upload a file, get analysis back, render a page. FastAPI is **streaming** —
a socket held open for minutes with audio flowing continuously. Forcing both
into one framework would make one of them awkward.

The two outbound arrows are the only traffic that leaves the machine's own
processes: a JSON POST to Ollama on localhost, and a fetch of whatever link is
being tested. Nothing is sent to a cloud service — the model runs on this
hardware.

---

## 2. Core audio concepts

Everything downstream depends on these, so they are worth understanding first.

### PCM, sample rate, bit depth

Digital audio is a list of numbers measuring air pressure, taken many times per
second.

- **Sample rate** — measurements per second. This project uses **16,000 Hz**.
- **Bit depth** — size of each number. **16-bit signed** → range −32,768…32,767.
- **Channels** — **mono** (one stream) throughout.

That gives the constant used everywhere:

```
16000 samples/sec × 2 bytes/sample × 1 channel = 32,000 bytes per second
```

So 64,000 bytes is exactly 2 seconds of audio. That single number explains the
buffering thresholds in the streaming server.

### Why 16 kHz

The **Nyquist theorem**: a sample rate of *N* can represent frequencies up to
*N/2*. At 16 kHz the ceiling is 8 kHz — comfortably above the ~4 kHz that
carries speech intelligibility, and it is what Whisper was trained on. Higher
rates would cost CPU and storage for information speech models discard anyway.

### WAV files

A WAV is a 44-byte header followed by raw PCM. The header declares sample rate,
channels, bit depth and data length. The Android app builds this by hand in
`Analyzer.kt` because it records raw PCM and the server needs a real file:

```
"RIFF" | size | "WAVE" | "fmt " | 16 | PCM=1 | channels | rate |
byte rate | block align | bits | "data" | data size | <raw PCM…>
```

Everything is **little-endian** here — an x86/ARM convention where the least
significant byte comes first.

### mu-law (the Twilio path)

Telephony does not send 16-bit PCM. It uses **mu-law**, a compression scheme
that squeezes 16-bit samples into 8 bits by spacing quantisation steps
logarithmically — fine detail at low volumes where the ear is sensitive, coarser
at high volumes where it isn't. Half the bandwidth, minimal perceived loss.

Twilio streams 8 kHz mu-law, so the server decodes and upsamples:

```python
pcm8  = audioop.ulaw2lin(base64.b64decode(payload), 2)   # mu-law → PCM16
pcm16 = audioop.ratecv(pcm8, 2, 1, 8000, 16000, state)   # 8 kHz → 16 kHz
```

`ratecv` carries **state** between calls — resampling needs continuity across
chunk boundaries, or you get a click at every seam.

---

## 3. The audio pipeline (Django)

Four stages, in `cyber/`. Each one exists for a reason.

### Stage 1 — Standardise (`audio_preprocessing.py`)

Any input (`.wav .mp3 .m4a .flac`) becomes 16 kHz mono WAV.

- **pydub** shells out to **ffmpeg** to decode compressed formats.
- **librosa** loads and resamples.
- Rejects empty audio, pure silence, and anything under `MIN_DURATION = 2`
  seconds.

Everything after this can assume one uniform format — a large simplification.

### Stage 2 — Noise reduction (`noise_reduction.py`)

**Spectral subtraction**, done deliberately gently.

1. **STFT** (Short-Time Fourier Transform, `N_FFT=1024`, `HOP_LENGTH=256`)
   converts the waveform into a time–frequency grid: how much energy at each
   frequency, moment by moment.
2. The **first 0.5 seconds** are assumed to be background noise and averaged
   into a noise profile — recordings usually start before anyone speaks.
3. Each frequency bin is attenuated in proportion to how much of it is noise.
4. **Inverse STFT** rebuilds the waveform.

Two safety limits matter:

```python
minimum_gain = 0.25   # never remove a frequency entirely
strength     = 0.5    # conservative subtraction
```

Aggressive denoising would strip exactly the subtle artefacts a deepfake
detector needs. The comment in the source says so explicitly.

### Stage 3 — Silence trimming (`audio_segmentation.py`)

`librosa.effects.trim(top_db=30)` removes leading and trailing quiet — anything
more than 30 dB below the loudest point. Only the ends; internal pauses are
speech rhythm and are kept.

### Stage 4 — Segmentation

Fixed **4-second** chunks, the final one zero-padded. Classifiers want uniform
input tensors; variable-length audio is awkward to batch. These segments are
the intended input to a detection model.

**Order matters and is not obvious:** trimming and segmentation run on the
**noise-reduced** audio, but the acoustic measurements (§6) run on the
**standardised** audio from stage 1 — denoising reshapes the spectrum and would
corrupt bandwidth and flatness readings.

---

## 4. The streaming server (FastAPI, `code.py`)

Live audio over a WebSocket, transcribed by Whisper.

### Binary framing

TCP is a byte stream with no message boundaries, so every audio frame carries a
20-byte header the server can parse:

```
struct format ">2sBBQII"          >  = big-endian (network byte order)

 magic "CS"   2 bytes   rejects garbage / desync
 channel      1 byte    1=uplink 2=downlink 3=stereo 4=mic
 encoding     1 byte    0 = raw PCM16
 timestamp    8 bytes   milliseconds
 sequence     4 bytes   ordering / loss detection
 length       4 bytes   payload size
 ────────────────────
 20 bytes + PCM payload
```

The magic bytes are the cheap insurance: if a frame doesn't start with `CS`,
the stream has desynchronised and the frame is dropped rather than fed to the
decoder as noise.

### Chunking

Audio accumulates per channel and transcribes when **either**:

- 64,000 bytes (2.0 s) have arrived, **or**
- 2.5 s passed with at least 16,000 bytes (0.5 s) buffered

The second condition prevents the tail of speech sitting in a buffer forever
when someone stops talking.

### Two non-obvious bugs found here

**1. Blocking the event loop.** `whisper_model.transcribe()` is synchronous CPU
work. Called directly inside `async def`, it stops the socket being read while
it runs; if transcription is slower than real time the backlog grows without
bound until the connection dies. Fixed with `asyncio.to_thread`.

The subtlety: `transcribe()` returns a **generator**. Wrapping only the call
would move nothing off the loop, because the work happens when the generator is
iterated. The whole consume has to live in the worker thread:

```python
def transcribe(audio_np):
    segments, _ = whisper_model.transcribe(audio_np, vad_filter=True, ...)
    return " ".join(s.text.strip() for s in segments).strip()   # iterated HERE
```

**2. Whisper hallucinates on silence.** Fed near-silent audio it invents text —
`"You"`, `"Thank you."`, `"Good, good."` were all observed in the logs. Fixed
with `vad_filter=True`, which runs **Voice Activity Detection** to discard
non-speech before decoding.

### The monitor window

A Tkinter dashboard shows connection state, per-channel frame counts, a live
level meter in dB, and transcripts.

Tkinter must own the main thread, so uvicorn runs in a daemon thread and the two
communicate through a `queue.Queue`, drained by `root.after(100, …)`.
**Consequence:** closing the window ends `mainloop()`, the daemon thread dies
with the process, and the server stops. That is by design, not a crash.

The level meter earned its place immediately: frame counters look identical
whether audio is speech or digital silence. Only the dB reading distinguishes
them — which is how the Android limitation below was proven rather than assumed.

---

## 5. The Android app

Kotlin, no XML layouts (UI built programmatically), one dependency (OkHttp —
Android has no built-in WebSocket client).

| Feature | Mechanism |
|---|---|
| **SCAN & DETECT** | Folder picker → newest audio file → HTTP upload |
| **RECORD & ANALYSE** | `AudioRecord` → WAV in memory → upload |
| **SCAN MESSAGES** | Reads inbox SMS → JSON POST |
| **START STREAMING** | `AudioRecord` → WebSocket frames |

### Folder access (Storage Access Framework)

Android has no free filesystem access. `ACTION_OPEN_DOCUMENT_TREE` asks the user
to grant one folder, returning a **tree URI**. `DocumentsContract` then queries
its children.

Critically, `takePersistableUriPermission()` is required — without it the grant
dies with the process and scanning fails after a restart.

### CSRF from a native app

Django's `CsrfViewMiddleware` rejects POSTs without a matching token. A native
app has no HTML form to read one from. Rather than disable the protection, the
app does what a browser does:

1. `GET /` and read the `csrftoken` **cookie** from `Set-Cookie`
2. POST with that value in **both** the `Cookie` header and `X-CSRFToken`

No `@csrf_exempt`, and the browser UI keeps its protection. (The SMS endpoint
*is* exempt — it is a pure JSON API with no session to hijack.)

### The finding that changed the project

**Android will not give a third-party app call audio.** This was measured, not
assumed.

- `VOICE_CALL`, `VOICE_UPLINK`, `VOICE_DOWNLINK` require the
  `CAPTURE_AUDIO_OUTPUT` permission, which is `signature|privileged` — only
  system apps or apps signed with the platform key can hold it.
- `MediaProjection` audio capture explicitly excludes
  `USAGE_VOICE_COMMUNICATION`.
- While a call is active, the OS **silences** non-privileged recorders. The app
  keeps streaming perfectly; every PCM buffer is zeros.

The level meter made this visible — the server logged
`DIGITAL SILENCE (all zeros)` throughout a live call, while the same app
produced healthy −6 to −24 dB readings outside one.

**Consequences:**

1. Only one mixed channel is available (the mic), never separate uplink/downlink.
   On speakerphone the mic picks up both sides acoustically.
2. The real path forward is **VoIP** — hence the Twilio endpoint. Cloud
   telephony delivers `inbound` and `outbound` as separate tracks, which is what
   the channel design assumed all along.
3. For existing calls, the working route is Samsung's own call recorder writing
   `.m4a` files, which **SCAN & DETECT** then uploads. This is what the app
   actually does today.

Also fixed along the way: `MediaRecorder.AudioSource.MIC` is used rather than
`VOICE_COMMUNICATION`, because the latter applies echo cancellation that treats
the caller's speakerphone voice as echo and removes exactly the half you want.

---

## 6. Acoustic analysis — and the honesty problem

The dashboard originally displayed **"DeepFake Probability: 94.8%"** with an
explanation about "artificial artifacts in the 12kHz range". This was
hardcoded HTML. It showed the same number for every file, and before any upload
at all. Nothing computed it.

It is replaced by `audio_features.py`, which measures real properties:

| Measurement | Meaning |
|---|---|
| **Bandwidth** | Highest frequency carrying real energy. Vocoders and low-bitrate codecs leave a hard ceiling. |
| **Spectral centroid** | The spectrum's "centre of mass" — perceived brightness. |
| **Spectral rolloff (95%)** | Frequency below which 95% of energy sits. |
| **Spectral flatness** | Noise-like (→1) vs tonal (→0). |
| **Zero-crossing rate** | How often the waveform crosses zero; tracks noisiness. |
| **Dynamic range** | Spread between loud and quiet passages. |
| **Crest factor** | Peak vs RMS — how "peaky" the signal is. |
| **Silence ratio** | Proportion of quiet frames; natural speech has pauses. |
| **MFCC variance** | Timbral variation. Synthetic delivery varies less. |

A small set of readable heuristics turns these into a score **with its reasons
attached**. Measured results:

```
REAL Samsung call recording
  bandwidth 7546 Hz (94% of available) · dynamic range 76.3 dB
  silence 38.9% · MFCC variance 2460.1
  INDICATOR 0/100 (low) — no synthetic-audio indicators matched

Synthetic sine tone
  bandwidth 296 Hz of 8000 · dynamic range 10.0 dB · silence 0.0%
  INDICATOR 60/100 (high)
    - energy stops at 296 Hz of a possible 8000 Hz
    - level is unusually uniform (10.0 dB spread)
    - almost no silence (0.0%)
```

**This is not a deepfake classifier.** It is signal analysis. A high score means
"this audio has properties often seen in synthesised or re-encoded material" —
not "this is a deepfake". Ordinary phone-call audio is band-limited too, which
would flag a genuine call. That is precisely why the **reasons are displayed
instead of hidden behind a percentage**: a number alone invites false
confidence, a reason can be argued with.

**A trained classifier now sits alongside this**, and the two are deliberately
kept apart on screen. The heuristics above are measured from the audio and never
change; the model verdict comes from whichever detector is selected. When both
are shown, the heuristic panel is the auditable half.

See [PRESENTATION.md](../PRESENTATION.md) §3 for the detector itself — the three
models, the bandwidth trap that makes a 11% EER model call real phone calls fake,
and the measurements behind both claims.

---

## 7. Scam message detection (`scam_detection.py`, `language.py`)

Ten readable regex rules plus link, sender and capitalisation checks. Each
contributes points; matched rules are returned as text. Every rule fires in
**English, Hindi and Marathi** — see *Three languages* below.

| Signal | Points |
|---|---|
| Asks for OTP / PIN / password | 30 |
| Prize or lottery claim | 25 |
| KYC / account-suspension pressure | 25 |
| Shortened link (bit.ly etc.) | 25 |
| Urgency or deadline pressure | 20 |
| Instructs you to click a link | 20 |
| Bank or card details requested | 15 |
| Contains a link | 15 |
| Impersonates a bank or authority | 10 |
| Sent from a personal number, not a sender ID | 5 |

Score → `low` (<30), `medium` (30–59), `high` (≥60).

```
+919876543210   HIGH 100
"URGENT: Your SBI account will be suspended. Complete KYC now at
 http://bit.ly/kyc or share your OTP."
Why: asks for OTP / PIN / password · KYC pressure · urgency ·
     impersonates a bank · shortened link hides its destination

MUM             LOW 0    "Call me when you're free"
```

Deliberately rule-based: every point is traceable, and the reasons are
auditable. Tuned for Indian-style bank/KYC/lottery scams.

### Three languages

Indian scam SMS arrive in English, Hindi and Marathi — and in practice in all
three at once: Devanagari, romanised (*"aapka khata band ho jayega"*), or a
Hindi sentence with `KYC` and `OTP` sitting in it in Latin script. That mixing
is normal, so it is not itself a signal.

**The rules** carry the other two languages inside the same patterns, under the
same labels and the same points. One rule, three languages:

```python
(30, "asks for OTP / PIN / password",
     r"\b(otp|one[\s-]?time[\s-]?password|…|mpin)\b"
     r"|ओटीपी|पासवर्ड|पिन\s*(कोड|नंबर)?|गुप्त\s*(शब्द|क्रमांक)"),
```

Not a parallel Hindi scorer: a second scoring path would be a second thing to
keep in step, and the reasons stay in English because the analyst reading the
dashboard does.

**Which language it is** — `language.py`, stdlib only:

1. **Script** from the Unicode block. Devanagari or Latin. Exact, no guessing.
2. **Marker words** to separate Hindi from Marathi, which share the script *and*
   much of their vocabulary. The split is done on the everyday words neither
   language borrows: Marathi *आहे / नाही / तुमच्या / करा*, Hindi *है / नहीं /
   आपका / करें*. The same markers are listed in Latin for romanised SMS.

A language-ID library was the obvious reach and the wrong one: `langdetect`
and friends are trained on paragraphs of prose, while an SMS is one line, half
of it digits and a bank name. They are weakest on exactly this input, and on
the hi/mr pair specifically.

URLs and digits are stripped before detection — `http://sbi-kyc.top` is not
evidence of English, it is evidence of nothing.

When the script is Devanagari but no marker appears (`"सूचना"`), the answer is
**"Hindi or Marathi"**, shown as `HI/MR`. Same principle as everywhere else
here: an honest *don't know* beats a coin flip presented as a result.

```
आपका SBI खाता बंद कर दिया जाएगा। तुरंत केवायसी करें…   HI  mixed
   markers आपका · करें · खाता · तुरंत          rules HIGH 90

अभिनंदन! तुम्ही ५०,००० रुपये लॉटरी जिंकली आहे…        MR  devanagari
   markers आहे · तुम्ही · त्वरित · पाठवा        rules HIGH 90

Tumhi kuthe aahe? Mi station var pohochlo aahe.      MR  latin
   markers aahe · tumhi                        rules LOW 0
```

The detected language is shown as a chip on every message card, with the
markers that decided it in the tooltip. It is also handed to the LLM, so the
model is not spending part of its answer guessing the language while judging
the content.

**A real bug this found:** `\b` does not work on Devanagari. `आपका` ends in a
combining vowel sign (Unicode category `Mn`), which Python's `\w` does not
count as a word character — so `\b` finds no boundary there and *every marker
missed silently*. The boundary is spelled out as "no Devanagari either side"
instead. The selftest caught it; nothing in the output would have.

One deliberate non-change: the SHOUTING rule is caseless-blind by nature.
Devanagari has no upper case, so it simply never fires there — no language
check needed to skip it.

### Where the rules fall short

The rules key off words, and some words appear in both scams and genuine
messages. A real Union Bank OTP alert scores **medium 40** — it says "OTP" and
mentions an amount. Nothing is wrong with the rule; the sentence really does
contain those things. Regex cannot tell *"here is your code"* from *"send me
your code"*.

That gap is what the URL scanner (§8) and the local LLM (§9) exist to close:
one follows the link and reports what is actually at the other end, the other
reads the intent of the sentence. Neither replaces the rules.

### Links inside a message

A message that contains a link is only as safe as where the link goes, so
`views.scan_links()` runs every URL in the body through the full inspector of
§8 **before** anything judges the text. Capped at 2 links per message —
otherwise a message with twenty links holds the request open while each is
fetched.

### The combined verdict

The dashboard shows one yes/no. Either half can raise it; neither can veto the
other:

```python
scam = bad_link or rule_level == "high" or llm_says_scam
```

`bad_link` is the scanner's own verdict on the link — its shape plus whatever
happened when the scanner tried to visit it — coming out high. That and a rule
score of ≥60 are the auditable half: they do not need the model's agreement,
and a small model regularly withholds it. The model can only *add* detections
the other two missed.

Measured, on real messages from the phone and two synthetic scams:

```
VA-UNIONB-T    "418181 is OTP to complete the transaction of Rs.10000.0 …
                PLS DO NOT SHARE WITH ANYONE. - Union Bank of India"
   rules   medium 40   (asks for OTP · money amount mentioned)
   links   none
   LLM     NOT SCAM  "a bank transaction alert from a registered sender"
   VERDICT NOT SCAM                                              ✓  2.9 s

+919876543210  "URGENT: Your SBI account is BLOCKED. Complete re-KYC now
                at http://sbi-kyc-verify.top/login …"
   rules   high 100
   links   http://sbi-kyc-verify.top/login → HIGH, domain does not resolve
   LLM     SCAM
   VERDICT SCAM                                                  ✓  4.1 s

+918888777666  "Congratulations! You have won Rs.50,000. To claim, reply
                with the OTP we just sent and your account number."
   rules   high 70
   links   none
   LLM     SCAM  "announces an unexpected prize and asks for sensitive info"
   VERDICT SCAM                                                  ✓  3.4 s
```

The middle case is the one that shaped the design: the link's domain does not
even exist, which the scanner establishes by trying to resolve it. With
`llama3.2` selected the model called that message *not* a scam — and the
verdict was still SCAM, because the link scan does not need its agreement.

---

## 8. URL inspection (`url_inspection.py`)

The URL tab used to be an input box and a button wired to nothing. It now does
two things, and reports them separately.

**Read the address** — free and instant. Shape, host, TLD, path:

| Signal | Points |
|---|---|
| An `@` in the address (hides the real host) | 30 |
| Brand name in the host that does not own the domain | 30 |
| Raw IP instead of a domain | 25 |
| Punycode host (`xn--`) — lookalike letters | 25 |
| Known shortener hiding the destination | 25 |
| Link straight to an installable file (`.apk`, `.exe`…) | 25 |
| Plain `http` — not encrypted | 15 |
| Cheap / abused TLD (`.top`, `.xyz`, `.zip`…) | 15 |
| Path pushes login / verify / KYC / claim | 15 |
| Non-standard port · deep subdomain chain · padded domain | 10 each |
| Another URL buried in the query string | 10 |

**Visit the address** — this is what answers *"is this a real website"*:

- does the domain resolve, and to what IP
- does the TLS certificate actually validate (a failure is a 30-point signal)
- the full redirect chain, and whether it ends on a different domain (+15)
- HTTP status, `Server` header, content type
- the page `<title>` — the cheapest "is this really the bank" check there is
- whether the page asks for a password, or hands you a download (+25)

Same output contract as the message scorer: score, level, and the reasons that
produced it.

```
https://www.wikipedia.org/                     LOW 0
  resolves 103.102.166.224 · HTTPS certificate valid · HTTP 200
  title "Wikipedia"

http://sbi-kyc-verify.top/login                HIGH 100
  plain http · .top is heavily abused · domain padded with hyphens
  uses the name 'sbi' but the domain is sbi-kyc-verify.top, not theirs
  path pushes a login / verify action · the domain does not resolve
```

### The visit is a trust boundary

The server fetches a URL a user typed. That is a request-forgery primitive if
left open, so `_guard()` runs before every fetch **and again on every redirect
hop** — a public host can 302 to `127.0.0.1` and `urllib` would follow it
happily:

- `http`/`https` only, never `file:` or anything else
- the resolved IPs must be public — private, loopback, link-local, reserved and
  multicast ranges are refused, and the refusal is reported as a finding rather
  than swallowed
- 200 KB read cap, 8-second timeout

A blocked host is shown as *"internal address, not visited"* — the dashboard
must not become a port scanner for the LAN it runs on.

---

## 9. The local LLM (`llm.py`)

An optional second opinion from a model running on this machine, configured in
the **LLM** tab: host, model (read live from `/api/tags`), timeout, on/off, and
a Test button. Settings live in one JSON file next to the other state.

Stdlib only — `urllib` posting JSON to `localhost:11434`. An HTTP library
dependency for one POST to loopback would be silly.

### What it is asked

One question: **is this a scam, yes or no** — plus an explanation, the
scanner's link findings so it judges the real destination instead of guessing
from the sentence around it, and the detected language (§7).

The prompt states that the message may be English, Hindi or Marathi, in
Devanagari or romanised or mixed, that the mixing is normal rather than
suspicious, and that the explanation must come back in English regardless —
the dashboard is English.

The verdict is **binary by design**. An earlier version showed
`SCAM 80%`; a percentage from a 4B model is decoration that reads as precision
the thing does not have. `{"scam": true|false}` is the whole answer.

### Making a small model usable

Local models this size misbehave in specific, repeatable ways, and every one of
these was hit in testing:

| Failure | Handling |
|---|---|
| Answers with `"verdict": "phishing"` instead of the field asked for | vocabulary fallback maps fraud/phish/malicious → scam |
| Wraps the JSON in prose or a ```` ```json ```` fence | first `{`…last `}` extraction |
| Emits a `<think>…</think>` block first (qwen, deepseek) | stripped before parsing |
| Says *"not a scam"* and then lists red flags | the answer wins, the flags are dropped |
| Says *"not a scam"* about a textbook phishing SMS | rules and the link scan outvote it (§7) |

Everything returns `{"error": ...}` instead of raising, so the dashboard keeps
working when Ollama is off, the model is missing, or a call times out.

### Model choice matters more than the prompt

Same prompt, same four messages — two genuine bank/university OTP alerts and
two obvious scams (prize+OTP, disconnection threat):

| Model | Correct alone | Failure mode |
|---|---|---|
| **mistral:7b** | **4/4** | — |
| llama3.2:latest | 2/4 | calls everything *not* a scam, including prize+OTP |
| qwen3:4b-instruct | 2/4 | calls everything a scam, including real bank alerts |

All three at ~3–4 s per message. `mistral:7b` scores 6/6 on the wider suite
with clean explanations, and is what the settings now point at.

This is also why the rules keep the floor. With `llama3.2` selected the
*combined* verdict was still 6/6 — rules and the link scan carried it while the
model contributed nothing. A system that trusted the model alone would have
been wrong four times out of six.

### Where it appears

- **Message tab** — click any card from the phone to analyse it, or **Analyze
  all (N)** to walk the queue one at a time (Ollama serialises anyway; a burst
  of ten would queue while eating memory). A card marks itself pending *before*
  the request goes out, so the 5-second poll that rebuilds the list cannot wipe
  an answer in flight.
- **URL tab** — an opinion on the link, given the scanner's facts.
- **LLM tab** — Test runs a known scam string end to end and times it.

The UI never names the model, only "LLM" — the model name belongs in the one
dropdown that selects it.

---

## 10. The dashboard

Django templates, vanilla JS, no framework.

**The problem it had:** the page could only ever show *its own* uploads. When the
phone posted a recording, the browser was never told and never asked —
`grep -c "setInterval|WebSocket|EventSource|poll"` returned **0**.

**The fix:** a `/recent/` endpoint plus polling every 3 seconds. Uploads from
any client now appear within seconds, badged **PHONE** or **BROWSER** —
distinguished by whether the POST carried a `csrfmiddlewaretoken` field, which
only the browser form sends.

Files in the live list are clickable to re-run the pipeline. That endpoint takes
an untrusted filename, so it is guarded on three levels — `basename()`,
extension allow-list, and a check that the resolved real path is genuinely
inside `uploads/`. Verified against `../settings.py`, `/etc/passwd`,
`..\..\db.sqlite3` and others.

Six tabs: Dashboard, Voice Detection, Scam Message, URL Analysis, Forensic
Reports, LLM.

### Rendering rules the UI follows

Two of these were bugs before they were rules:

- **State lives in a map, not the DOM.** The message list is rebuilt from
  scratch every 5-second poll. Any verdict held only in the HTML would vanish
  on the next tick, so verdicts and in-flight "pending" markers live in a
  JS object keyed by sender+body, and the poll redraws from it.
- **One request per message, whatever the entry point.** Clicking a card and
  "Analyze all" both route through one `askModel()` that returns early if the
  message already has an answer or a request in flight.
- **Everything from the phone or the network is escaped.** Message bodies,
  senders, page titles and URLs all pass through `esc()` before reaching
  `innerHTML` — the SMS body is attacker-controlled text.

### Endpoints

| Route | Purpose |
|---|---|
| `GET /` | Dashboard |
| `POST /upload-audio/` | Upload and process |
| `GET /recent/` | Live state — last result, file list, stats |
| `POST /process-existing/` | Re-run pipeline on a stored file |
| `GET/POST /messages/` | SMS batch in / out |
| `POST /analyse-message/` | Score one message, scan its links, optional LLM |
| `POST /analyse-url/` | Inspect and visit one URL, optional LLM |
| `GET/POST /llm-settings/` | Read / write LLM config, list installed models |
| `POST /llm-test/` | Run a known scam string through the LLM, timed |

---

## 11. Running it

```
Deepfake Voice Detection\run.bat     →  http://127.0.0.1:8000   (dashboard)
run.bat                              →  ws://…:8765/ws/audio    (streaming)
```

Each `.bat` creates its own virtualenv on first run, installs dependencies, and
starts the server. Two separate venvs on purpose: `librosa` pulls in
`numba`/`scipy` with tight numpy pins that could break the Whisper install.

`ffmpeg` is bundled locally in `Deepfake Voice Detection/ffmpeg/bin` and added to
`PATH` by the launcher — pydub shells out to it for mp3/m4a/flac, and nothing
system-wide is modified.

The phone needs the PC's LAN IP, so Django binds `0.0.0.0:8000`,
`ALLOWED_HOSTS` permits it, and firewall rules open 8000 and 8765 to the local
subnet only.

### Verification without a server

```
cd detection
..\.venv\Scripts\python verify_dashboard.py        # 26 end-to-end checks
..\.venv\Scripts\python cyber\language.py          # hi / mr / en detection
..\.venv\Scripts\python cyber\scam_detection.py    # rule selftest, 3 languages
..\.venv\Scripts\python cyber\url_inspection.py    # URL heuristics, offline
..\.venv\Scripts\python cyber\llm.py               # LLM parsing / verdict shape
..\.venv\Scripts\python cyber\audio_features.py    # measurement selftest
python code.py --selftest                          # wire format + level maths
```

`verify_dashboard.py` uses Django's in-process test client, so it exercises
templates, URLs, uploads and JSON endpoints without binding a port.

---

## 12. Honest status

**Works, verified on real hardware**

- Audio pipeline: standardise → denoise → trim → segment
- Phone folder scan → upload → analysis (tested on real Samsung `.m4a` calls)
- Live dashboard reflecting uploads from any client
- SMS scam scoring with explanations, in English, Hindi and Marathi
- Language detection (script + marker words), **7/7** on a mixed-language suite
- URL inspection — heuristics plus a real visit (DNS, TLS, redirects, title)
- Local LLM second opinion via Ollama, configurable in the UI
- Combined message verdict: **6/6** on a suite of three real bank/university
  SMS and three synthetic scams, and **7/7** on the Hindi/Marathi/English
  suite (Devanagari, romanised and mixed), 3–6 s each
- Acoustic measurement with explained indicators
- Whisper streaming and transcription (outside calls)
- Twilio Media Streams endpoint, verified with a synthetic client

**Added since this section was first written** (see
[PRESENTATION.md](../PRESENTATION.md))

- **A deepfake classifier** — three selectable models: two trained here on
  ASVspoof 2019 LA and Fake-or-Real, plus an optional pretrained
  WavLM+AASIST detector. Training runs from the dashboard, with speaker-safe
  splits and cross-dataset evaluation.
- **A real forensic log** — every analysis writes an immutable report with its
  timestamp, evidence, risk band, verdict and the model that produced it.

**Not implemented**

- **Live call capture.** Blocked by Android at the OS level; VoIP is the route.
- **Twilio in production.** Needs a public `wss://` URL and signature
  validation on `/twiml`.
- **Domain age / reputation lookup.** The URL scanner sees what a domain *is*,
  not how long it has existed or who registered it — that needs WHOIS or a
  threat feed. A day-old lookalike with a valid certificate scores low on shape
  alone.
- **The LLM on phone batches.** Rules score all 10 messages instantly; the
  model runs only on the one you click, or the queue you start. Ten messages
  through a 7B model is ~40 s, past a sane request timeout.

**Still mock data**

- The dashboard overview statistics (Total Scans, AI Voices Detected, ...)

The Forensic tab's hardcoded "DeepFake Detected (94.8%)" table is gone — it now
reads from a real report log. The sign-in screen is a real Django auth gate; the
dashboard view is `@login_required`.

**Known limits worth stating plainly**

- A rule score of `medium` on a genuine bank OTP alert is expected, not a bug
  (§7). The LLM panel is what answers scam yes/no; the rule score stays visible
  because it is the auditable half.
- The public-suffix list in `url_inspection.py` is hand-written for the TLDs in
  scope. Domains under an unlisted multi-part suffix get their registrable
  domain computed wrong.
- Verdict quality is bounded by the model selected. `llama3.2` alone answers
  "not a scam" to everything (§9).
- Language support is Hindi, Marathi and English only. Anything else — Tamil,
  Bengali, Gujarati — falls through to `unknown`, and only the English half of
  each rule can fire on it. Adding a language means adding its markers and its
  patterns, not retraining anything.
- Hindi/Marathi separation rests on marker words. A very short Devanagari
  message carrying none of them returns "Hindi or Marathi" rather than
  guessing, which is correct but is still a non-answer.

---

## 13. The principle worth keeping

The most valuable change in this project was not a feature. It was replacing
invented numbers with measured ones, and showing the reasoning next to the
result.

A dashboard that says `94.8%` for every file looks more finished than one
saying `0/100 — no indicators matched`. It is also useless, and worse than
useless if anyone acts on it. Every number now on screen came from something
that actually ran, and every judgement shows its reasons so a human can
disagree with it.

Adding the LLM tested the same principle a second time. The first version of
its panel read `SCAM 80%` — and the 80% was invented in exactly the way the
hardcoded 94.8% had been, just by a model instead of by hand. A 4B model has no
calibrated confidence to report. So the percentage went, and what remains is a
yes/no it can actually answer plus the sentence explaining it.

The same reasoning shaped where the LLM sits in the system. It is a second
opinion, never the authority: it can raise an alarm the rules missed, and it
cannot talk one down. When it said "not a scam" about a phishing SMS whose link
did not even resolve, the link scan overruled it — because that finding came
from resolving a domain and fetching a page, and the model's came from reading
a sentence.

Where something isn't built, the interface says so.
