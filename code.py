
import asyncio
import audioop  # ponytail: removed in Python 3.13 — swap for audioop-lts or numpy mu-law if you upgrade
import base64
import json
import math
import queue
import socket
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import scrolledtext

import numpy as np
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel

# Channel labels contain emoji; a plain cmd.exe console is cp1252 and raises
# UnicodeEncodeError on the first transcript, killing the handler mid-stream.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = FastAPI(title="CallStream Audio & Whisper Server")

# Channel identifiers matching CallStream Android App
CHANNEL_NAMES = {
    1: "🟢 UPLINK (You)",
    2: "🟠 DOWNLINK (Caller)",
    3: "🔵 STEREO_COMBINED",
    4: "🎙️ MIC_TEST"
}
CHANNEL_COLORS = {1: "#4ade80", 2: "#fb923c", 3: "#60a5fa", 4: "#c084fc"}

HEADER_STRUCT = ">2sBBQII"
HEADER_SIZE = 20
BYTES_PER_SEC = 32000  # 16kHz, 16-bit, mono

# Server thread -> Tk thread. Tk may only be touched from the main thread.
EVENTS = queue.Queue()


def emit(kind, **data):
    EVENTS.put((kind, data))


def log(text, ch_id=None):
    print(text, flush=True)
    emit("line", text=text, ch_id=ch_id)


def transcribe(audio_np):
    """Blocking. Iterating `segments` is where the CPU work actually happens,
    so it must stay inside the worker thread, not just the transcribe() call."""
    # vad_filter drops non-speech before decoding; without it Whisper hallucinates
    # ("You", "Thank you.", "Good, good.") on every silent chunk.
    segments, _ = whisper_model.transcribe(
        audio_np, language="en", beam_size=1, vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()


class Chunker:
    """Per-channel PCM buffering, level metering and transcription.
    Shared by every audio source so the Android and Twilio paths cannot drift apart."""

    def __init__(self, send_json=None):
        self.buf = {}
        self.last = {}
        self.frames = {}
        self.total = {}
        self.muted = {}
        self.send_json = send_json

    async def feed(self, ch_id, pcm, seq=0):
        if ch_id not in self.buf:
            self.buf[ch_id] = bytearray()
            self.last[ch_id] = time.time()
        label = CHANNEL_NAMES.get(ch_id, f"Channel {ch_id}")

        self.buf[ch_id].extend(pcm)
        self.frames[ch_id] = self.frames.get(ch_id, 0) + 1
        self.total[ch_id] = self.total.get(ch_id, 0) + len(pcm)

        # peak level of this frame — the only readout that tells silence from speech
        whole = len(pcm) // 2 * 2
        peak = (int(np.abs(np.frombuffer(pcm[:whole], dtype=np.int16)).max()) / 32768
                if whole else 0.0)
        emit("stat", ch_id=ch_id, label=label, frames=self.frames[ch_id],
             total=self.total[ch_id], buffered=len(self.buf[ch_id]), seq=seq, peak=peak)

        # Transcribe every ~2s of audio, or after 2.5s idle with >=0.5s buffered
        now = time.time()
        if not (len(self.buf[ch_id]) >= 64000 or
                (now - self.last[ch_id] >= 2.5 and len(self.buf[ch_id]) >= 16000)):
            return

        pcm_data = bytes(self.buf[ch_id])
        self.buf[ch_id].clear()
        self.last[ch_id] = now

        audio_np = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0

        # all-zero PCM is the fingerprint of the OS muting a non-privileged recorder
        # (what Android does during a call); a quiet room still reads around -50 dB.
        chunk_peak = float(np.abs(audio_np).max())
        level = f"{20 * math.log10(chunk_peak):.1f} dB" if chunk_peak > 1e-5 else "silent"

        if chunk_peak <= 1e-5:
            # Decoding zeros is pure waste, and repeating the line every 2s buries
            # anything real. Say it once per mute, once again when audio returns.
            if not self.muted.get(ch_id):
                self.muted[ch_id] = True
                log(f"[{label}] DIGITAL SILENCE (all zeros) — the source is muted, "
                    f"skipping transcription until audio returns", ch_id=ch_id)
            return
        if self.muted.get(ch_id):
            self.muted[ch_id] = False
            log(f"[{label}] audio returned ({level})", ch_id=ch_id)

        started = time.time()
        # off the event loop, or the socket stops being read while Whisper runs
        text = await asyncio.to_thread(transcribe, audio_np)
        emit("timing", ch_id=ch_id, audio_s=len(pcm_data) / BYTES_PER_SEC,
             cpu_s=time.time() - started)

        if text:
            log(f"[{label}] ({level}) >> {text}", ch_id=ch_id)
            if self.send_json:
                await self.send_json({
                    "type": "transcription",
                    "channel": ch_id,
                    "channel_name": label,
                    "transcription": f"{label}: {text}",
                })
        else:
            log(f"[{label}] ({level}) no speech", ch_id=ch_id)


@app.websocket("/ws/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    await websocket.accept()
    log("\n[CallStream] Android device connected to WebSocket!")
    emit("status", text="Device connected", ok=True)

    chunker = Chunker(websocket.send_json)

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break  # normal hangup, not an error

            # Handle text control / ping frames
            if "text" in message:
                try:
                    payload = json.loads(message["text"])
                    msg_type = payload.get("type")

                    if msg_type == "handshake":
                        log(f"[Handshake] Device: {payload.get('device_model')}, "
                            f"Rate: {payload.get('sample_rate')}Hz, "
                            f"Mode: {payload.get('channels')}")
                        emit("status",
                             text=f"{payload.get('device_model')} @ {payload.get('sample_rate')}Hz",
                             ok=True)
                        await websocket.send_json({"type": "ack", "status": "ready"})

                    elif msg_type == "ping":
                        client_time = payload.get("client_time", 0)
                        await websocket.send_json({
                            "type": "pong",
                            "client_time": client_time,
                            "server_time": int(time.time() * 1000)
                        })
                except Exception as err:
                    log(f"[Warning] Text parse error: {err}")

            # Handle binary audio frames (PCM 16-bit 16kHz)
            elif "bytes" in message:
                raw_bytes = message["bytes"]
                if len(raw_bytes) < HEADER_SIZE:
                    emit("bad", reason="short frame")
                    continue

                magic, ch_id, encoding, ts_ms, seq, payload_len = struct.unpack(
                    HEADER_STRUCT, raw_bytes[:HEADER_SIZE]
                )

                if magic != b"CS":
                    emit("bad", reason=f"bad magic {magic!r}")
                    continue

                audio_pcm = raw_bytes[HEADER_SIZE: HEADER_SIZE + payload_len]
                await chunker.feed(ch_id, audio_pcm, seq)

        log("[CallStream] Android device disconnected.")
        emit("status", text="Waiting for device…", ok=False)
    except WebSocketDisconnect:
        log("[CallStream] Android device disconnected.")
        emit("status", text="Waiting for device…", ok=False)
    except Exception as e:
        log(f"[Error] WebSocket loop error: {e}")
        emit("status", text=f"Error: {e}", ok=False)


# --- Cloud telephony (Twilio Media Streams) -------------------------------------
# The two call legs arrive already separated, which is what the Android mic could
# never give us: "inbound" is the far end, "outbound" is your own agent.
TWILIO_TRACK_CHANNEL = {"inbound": 2, "outbound": 1}


@app.websocket("/ws/twilio")
async def twilio_media_stream(websocket: WebSocket):
    await websocket.accept()
    log("\n[Twilio] Media Stream connected.")
    emit("status", text="Twilio stream connected", ok=True)

    chunker = Chunker()          # Twilio's socket is one-way; nothing to send back
    resample_state = {}          # ratecv carries state per channel across frames
    seq = 0

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if "text" not in message:
                continue

            msg = json.loads(message["text"])
            event = msg.get("event")

            if event == "start":
                start = msg.get("start", {})
                log(f"[Twilio] call={start.get('callSid')} "
                    f"tracks={start.get('tracks')} format={start.get('mediaFormat')}")
                emit("status", text=f"Twilio call {str(start.get('callSid'))[:14]}…", ok=True)

            elif event == "media":
                media = msg["media"]
                ch_id = TWILIO_TRACK_CHANNEL.get(media.get("track"), 3)
                # Twilio ships 8kHz mu-law; Whisper wants 16kHz PCM16
                pcm8 = audioop.ulaw2lin(base64.b64decode(media["payload"]), 2)
                pcm16, resample_state[ch_id] = audioop.ratecv(
                    pcm8, 2, 1, 8000, 16000, resample_state.get(ch_id))
                seq += 1
                await chunker.feed(ch_id, pcm16, seq)

            elif event == "stop":
                break

        log("[Twilio] Media Stream ended.")
        emit("status", text="Waiting for device…", ok=False)
    except WebSocketDisconnect:
        log("[Twilio] Media Stream disconnected.")
        emit("status", text="Waiting for device…", ok=False)
    except Exception as e:
        log(f"[Error] Twilio stream error: {e}")
        emit("status", text=f"Error: {e}", ok=False)


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml(request: Request):
    """Point your Twilio number's webhook here. `both_tracks` is what splits the
    call into the two channels; the default is inbound only."""
    host = request.headers.get("host", "example.com")
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Say>This call is recorded and transcribed.</Say>'
        f'<Start><Stream url="wss://{host}/ws/twilio" track="both_tracks"/></Start>'
        '<Pause length="3600"/></Response>',
        media_type="application/xml")


def lan_ip():
    """IP of whichever interface holds the default route. Sends nothing — UDP connect
    only resolves the route, so it works with no internet."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def build_ui():
    root = tk.Tk()
    root.title("CallStream Monitor")
    root.geometry("1000x620")
    root.configure(bg="#111827")

    status = tk.Label(root, text="Waiting for device…", bg="#111827", fg="#9ca3af",
                      font=("Segoe UI", 13, "bold"), anchor="w", padx=12, pady=8)
    status.pack(fill="x")

    tk.Label(root, text=f"Point the phone at:  ws://{lan_ip()}:8765/ws/audio",
             bg="#111827", fg="#93c5fd", font=("Consolas", 11), anchor="w",
             padx=12, pady=2).pack(fill="x")

    stats_frame = tk.Frame(root, bg="#111827", padx=12, pady=6)
    stats_frame.pack(fill="x")
    stat_labels = {}

    text = scrolledtext.ScrolledText(root, bg="#0b1120", fg="#d1d5db", insertbackground="#d1d5db",
                                     font=("Consolas", 10), wrap="word", borderwidth=0)
    text.pack(fill="both", expand=True, padx=8, pady=8)
    for ch_id, color in CHANNEL_COLORS.items():
        text.tag_config(f"ch{ch_id}", foreground=color)
    text.tag_config("dim", foreground="#6b7280")

    def add_line(msg, tag="dim"):
        text.insert("end", msg.strip() + "\n", tag)
        text.see("end")
        if float(text.index("end-1c").split(".")[0]) > 500:
            text.delete("1.0", "100.0")

    def drain():
        while True:
            try:
                kind, d = EVENTS.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                status.config(text=d["text"], fg="#4ade80" if d["ok"] else "#9ca3af")
            elif kind == "line":
                ch = d.get("ch_id")
                add_line(d["text"], f"ch{ch}" if ch in CHANNEL_COLORS else "dim")
            elif kind == "stat":
                ch = d["ch_id"]
                if ch not in stat_labels:
                    lbl = tk.Label(stats_frame, bg="#111827", anchor="w",
                                   fg=CHANNEL_COLORS.get(ch, "#d1d5db"),
                                   font=("Consolas", 10))
                    lbl.pack(fill="x")
                    stat_labels[ch] = lbl
                peak = d["peak"]
                bar = "█" * int(peak * 24)
                db = f"{20 * math.log10(peak):>6.1f} dB" if peak > 0.0001 else "  -inf"
                stat_labels[ch].config(
                    text=f"{d['label']:<22} {d['frames']:>6} fr  "
                         f"{d['total'] / BYTES_PER_SEC:>6.1f}s  "
                         f"buf {d['buffered'] / BYTES_PER_SEC:>4.1f}s  "
                         f"seq {d['seq']:>6}  |{bar:<24}| {db}")
            elif kind == "timing":
                ratio = d["cpu_s"] / max(d["audio_s"], 0.01)
                warn = "  ⚠ SLOWER THAN REALTIME" if ratio > 1 else ""
                add_line(f"    {d['audio_s']:.1f}s audio transcribed in "
                         f"{d['cpu_s']:.1f}s ({ratio:.2f}x){warn}", "dim")
            elif kind == "bad":
                add_line(f"[dropped frame] {d['reason']}", "dim")

        root.after(100, drain)

    root.after(100, drain)
    return root


def selftest():
    """Fails if the wire format or the chunking thresholds drift."""
    assert struct.calcsize(HEADER_STRUCT) == HEADER_SIZE
    pcm = b"\x01\x02" * 100
    frame = struct.pack(HEADER_STRUCT, b"CS", 2, 0, 1234, 7, len(pcm)) + pcm
    magic, ch, enc, ts, seq, plen = struct.unpack(HEADER_STRUCT, frame[:HEADER_SIZE])
    assert (magic, ch, ts, seq, plen) == (b"CS", 2, 1234, 7, len(pcm))
    assert frame[HEADER_SIZE:HEADER_SIZE + plen] == pcm
    assert 64000 / BYTES_PER_SEC == 2.0 and 16000 / BYTES_PER_SEC == 0.5

    # a 440Hz tone at amplitude 6000 must read as 6000/32768 -> -14.7 dB
    tone = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * n / 16000)))
                    for n in range(1600))
    whole = len(tone) // 2 * 2
    peak = int(np.abs(np.frombuffer(tone[:whole], dtype=np.int16)).max()) / 32768
    assert abs(20 * math.log10(peak) - (-14.7)) < 0.5, peak

    # Twilio path: 8kHz mu-law in -> 16kHz PCM16 out, amplitude survives, length doubles
    mulaw = audioop.lin2ulaw(tone[:1600], 2)          # 800 samples @ 8kHz
    pcm8 = audioop.ulaw2lin(base64.b64decode(base64.b64encode(mulaw)), 2)
    assert len(pcm8) == 1600
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)
    assert abs(len(pcm16) - 3200) <= 4, len(pcm16)
    resampled_peak = int(np.abs(np.frombuffer(pcm16, dtype=np.int16)).max()) / 32768
    assert abs(20 * math.log10(resampled_peak) - (-14.7)) < 1.0, resampled_peak
    assert TWILIO_TRACK_CHANNEL == {"inbound": 2, "outbound": 1}
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)

    import uvicorn

    print("[Whisper] Loading Whisper model (base.en on CPU/CUDA)...")
    whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")
    print("[Whisper] Model loaded and ready!")

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8765))
    server.install_signal_handlers = lambda: None  # not the main thread
    threading.Thread(target=server.run, daemon=True).start()

    build_ui().mainloop()
