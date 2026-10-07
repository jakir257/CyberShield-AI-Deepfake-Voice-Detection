package com.codephoton.callstream

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.net.Uri
import android.provider.DocumentsContract
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit

/**
 * Two ways to get audio into the Deepfake Voice Detection server:
 *   scanAndUpload()          - newest recording in a user-picked folder (the normal path)
 *   start() / stopAndUpload() - record a fresh clip from the mic
 * Both end in the same upload() and populate the same status/result fields.
 */
object Analyzer {

    const val SAMPLE_RATE = 16000
    private const val MAX_SECONDS = 120
    private const val MIN_SECONDS = 2.0

    /** Must match SUPPORTED_FORMATS in the server's audio_preprocessing.py. */
    private val AUDIO_EXT = listOf(".wav", ".mp3", ".m4a", ".flac")

    @Volatile var recording = false
    @Volatile var busy = false
    @Volatile var status = "idle"
    @Volatile var result = ""
    @Volatile var lastFile = ""

    private var worker: Thread? = null
    private val pcm = ByteArrayOutputStream()

    // ----- live 4-second segments --------------------------------------------
    //
    // The brief: every 4 seconds, send a segment to the backend while the
    // recording is still running, so the flow can be watched working. The
    // whole clip is still uploaded at Stop - that is a separate thing and it
    // still gets the full pipeline.

    private const val SEGMENT_SECONDS = 4

    /** 4s x 16000 samples x 2 bytes. The buffer is cut at this size. */
    private const val SEGMENT_BYTES = SEGMENT_SECONDS * SAMPLE_RATE * 2

    @Volatile var segmentsSent = 0
    @Volatile var session = ""

    private val segmentPcm = ByteArrayOutputStream()
    private var segmentIndex = 0
    private var segmentBase = ""

    /** Short timeouts on purpose: a segment is worthless late, and a stalled
     *  connection must not pile threads up behind it. */
    private val segmentClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    // ----- connection heartbeat ----------------------------------------------
    //
    // Nothing holds a socket open to the PC, so neither side can *know* the
    // other is there. The phone says "still here" on a timer and the server
    // records when it last heard; both then show the same thing. A status that
    // says "last seen 40s ago" is worth more than a green light that lies.

    private const val PING_SECONDS = 10L

    /** true = the last ping reached the server. */
    @Volatile var connected = false

    /** What to show under the server field: "connected", or why not. */
    @Volatile var connectionStatus = "not checked yet"

    private var pinger: Thread? = null
    @Volatile private var pingUrl = ""

    private val pingClient = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(4, TimeUnit.SECONDS)
        .build()

    /**
     * Start (or re-point) the heartbeat. Safe to call whenever the server
     * address changes - the single background thread picks up the new URL.
     */
    fun startHeartbeat(baseUrl: String, device: String) {

        pingUrl = baseUrl.trimEnd('/')

        if (pinger != null) return          // already running, new URL is enough

        pinger = Thread {
            while (true) {
                val base = pingUrl
                if (base.isNotEmpty()) ping(base, device)
                try {
                    Thread.sleep(PING_SECONDS * 1000)
                } catch (e: InterruptedException) {
                    return@Thread
                }
            }
        }.apply { isDaemon = true; start() }
    }

    /** Ping once, right now, off the UI thread. For the TEST CONNECTION
     *  button - waiting up to 10s for the next beat is not an answer. */
    fun testNow(baseUrl: String, device: String) {
        val base = baseUrl.trimEnd('/')
        pingUrl = base
        Thread { ping(base, device) }.start()
    }

    private fun ping(base: String, device: String) {

        val body = JSONObject()
            .put("device", device)
            .put("version", "apk-v1")
            .toString()
            .toRequestBody("application/json".toMediaType())

        try {
            val req = Request.Builder().url("$base/ping/").post(body).build()

            pingClient.newCall(req).execute().use { resp ->
                connected = resp.isSuccessful
                connectionStatus = if (resp.isSuccessful) {
                    "connected to $base"
                } else {
                    "server answered HTTP ${resp.code}"
                }
            }
        } catch (e: Exception) {
            connected = false
            // the message people actually need: which address failed, and why
            connectionStatus = "cannot reach $base (${e.javaClass.simpleName})"
        }
    }

    // ----- folder scan -------------------------------------------------------

    /** Newest supported audio file in the picked tree, or null. */
    fun latestAudio(ctx: Context, tree: Uri): Triple<Uri, String, Long>? {
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(
            tree, DocumentsContract.getTreeDocumentId(tree)
        )
        var best: Triple<Uri, String, Long>? = null
        ctx.contentResolver.query(
            children,
            arrayOf(
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_LAST_MODIFIED
            ), null, null, null
        )?.use { c ->
            while (c.moveToNext()) {
                val name = c.getString(1) ?: continue
                if (AUDIO_EXT.none { name.lowercase().endsWith(it) }) continue
                val modified = c.getLong(2)
                val current = best
                if (current == null || modified > current.third) {
                    best = Triple(
                        DocumentsContract.buildDocumentUriUsingTree(tree, c.getString(0)),
                        name, modified
                    )
                }
            }
        }
        return best
    }

    /**
     * Upload exactly the audio file selected by the user in the Android
     * document picker. This deliberately does NOT scan the folder or choose
     * the newest/first file.
     */
    fun scanAndUpload(ctx: Context, fileUri: Uri, baseUrl: String) {
        if (busy || recording) return

        busy = true
        result = ""
        status = "reading selected file..."

        Thread {
            try {
                val name = queryDisplayName(ctx, fileUri)
                    ?: fileUri.lastPathSegment
                    ?: "selected_audio"

                if (AUDIO_EXT.none { name.lowercase().endsWith(it) }) {
                    status = "unsupported file: " + name
                    lastFile = ""
                    return@Thread
                }

                lastFile = name

                val bytes = ctx.contentResolver.openInputStream(fileUri)?.use {
                    it.readBytes()
                }

                if (bytes == null || bytes.isEmpty()) {
                    status = "could not read " + name
                    return@Thread
                }

                status = "uploading " + name + " (" + (bytes.size / 1024) + " KB)..."
                upload(baseUrl.trimEnd('/'), bytes, name)

            } catch (e: Exception) {
                status = "scan failed: " + (e.message ?: e.javaClass.simpleName)
            } finally {
                busy = false
            }
        }.start()
    }

    private fun queryDisplayName(ctx: Context, uri: Uri): String? {
        return ctx.contentResolver.query(
            uri,
            arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME),
            null,
            null,
            null
        )?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }
    }

    // ----- mic recording -----------------------------------------------------

    fun start(baseUrl: String = "") {
        if (recording || busy) return
        recording = true
        result = ""
        lastFile = ""
        status = "recording..."
        pcm.reset()

        // a new session id per recording, so the server can group this run's
        // segments and the dashboard can show them on their own
        segmentBase = baseUrl.trimEnd('/')
        session = "s" + System.currentTimeMillis()
        segmentIndex = 0
        segmentsSent = 0
        segmentPcm.reset()

        worker = Thread { capture() }.also { it.start() }
    }

    fun stopAndUpload(baseUrl: String) {
        if (!recording) return
        recording = false
        worker?.join(3000)
        worker = null

        val bytes = pcm.toByteArray()
        val seconds = bytes.size / (SAMPLE_RATE * 2.0)
        if (seconds < MIN_SECONDS) {
            status = "too short (%.1fs) - server needs at least %.0fs".format(seconds, MIN_SECONDS)
            return
        }
        busy = true
        lastFile = "recording.wav"
        status = "uploading %.1fs...".format(seconds)
        Thread {
            try {
                upload(baseUrl.trimEnd('/'), wav(bytes), "recording.wav")
            } finally {
                busy = false
            }
        }.start()
    }

    private fun capture() {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val recorder = try {
            AudioRecord(
                MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf, SAMPLE_RATE * 2)
            )
        } catch (e: SecurityException) {
            status = "mic permission denied"
            recording = false
            return
        }
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            status = "AudioRecord init failed"
            recording = false
            recorder.release()
            return
        }

        val cap = MAX_SECONDS * SAMPLE_RATE * 2
        val buf = ByteArray(3200)
        try {
            recorder.startRecording()
            while (recording && pcm.size() < cap) {
                val n = recorder.read(buf, 0, buf.size)
                if (n > 0) {
                    pcm.write(buf, 0, n)

                    // the same bytes accumulate a second time, and every time
                    // that buffer reaches 4 seconds it is cut loose and sent
                    if (segmentBase.isNotEmpty()) {
                        segmentPcm.write(buf, 0, n)
                        if (segmentPcm.size() >= SEGMENT_BYTES) flushSegment()
                    }
                }
            }
            if (pcm.size() >= cap) {
                recording = false
                status = "hit the " + MAX_SECONDS + "s limit"
            }
        } catch (e: Exception) {
            status = "capture error: " + e.message
        } finally {
            runCatching { recorder.stop() }
            recorder.release()

            // whatever is left over is a real partial segment, not a remnant -
            // send it so the last few seconds of a call are not silently lost
            if (segmentBase.isNotEmpty() && segmentPcm.size() > 0) flushSegment()
        }
    }

    /**
     * Cut the buffered audio into a segment and upload it.
     *
     * Fire and forget on its own thread: the capture loop must get straight
     * back to recorder.read(). Blocking it for the length of an HTTP request
     * would let the AudioRecord buffer overrun, and the audio dropped would be
     * exactly the audio we are trying to analyse.
     */
    private fun flushSegment() {

        val bytes = segmentPcm.toByteArray()
        segmentPcm.reset()

        if (bytes.isEmpty()) return

        val index = segmentIndex++
        val base = segmentBase
        val id = session

        Thread {
            val ok = uploadSegment(base, id, index, wav(bytes))
            if (ok) {
                segmentsSent++
                // only overwrite the status while still recording, or this
                // stamps over the final "ANALYSED" result
                if (recording) {
                    status = "recording... " + segmentsSent + " segments sent"
                }
            } else if (recording) {
                status = "recording... segment " + index + " failed to send"
            }
        }.start()
    }

    /** POST one segment. Returns false rather than throwing - a dropped
     *  segment must not take the recording down with it. */
    private fun uploadSegment(base: String, id: String, index: Int, bytes: ByteArray): Boolean {

        return try {
            val body = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("session", id)
                .addFormDataPart("index", index.toString())
                .addFormDataPart(
                    "audio", "seg_%04d.wav".format(index),
                    bytes.toRequestBody("audio/wav".toMediaType())
                ).build()

            val req = Request.Builder().url(base + "/upload-segment/")
                .post(body).build()

            segmentClient.newCall(req).execute().use { response ->
                if (!response.isSuccessful) return@use false

                val body = response.body?.string() ?: return@use true

                try {
                    val json = JSONObject(body)

                    val verdict = json.optString("verdict", "")
                    val probability = json.optDouble("fake_probability", -1.0)

                    if (verdict.isNotEmpty()) {
                        result = if (probability >= 0) {
                            "%s (%.1f%% fake)".format(
                                verdict.uppercase(),
                                probability * 100
                            )
                        } else {
                            verdict.uppercase()
                        }

                        status = "Segment %d: %s".format(index, result)
                    }
                } catch (_: Exception) {
                    // Upload succeeded even if result parsing failed
                }

                true
            }

        } catch (e: Exception) {
            false
        }
    }

    /** 44-byte canonical WAV header + raw PCM16 mono payload. */
    private fun wav(data: ByteArray): ByteArray {
        val h = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        h.put("RIFF".toByteArray())
        h.putInt(36 + data.size)
        h.put("WAVE".toByteArray())
        h.put("fmt ".toByteArray())
        h.putInt(16)
        h.putShort(1)
        h.putShort(1)
        h.putInt(SAMPLE_RATE)
        h.putInt(SAMPLE_RATE * 2)
        h.putShort(2)
        h.putShort(16)
        h.put("data".toByteArray())
        h.putInt(data.size)
        return h.array() + data
    }

    // ----- upload ------------------------------------------------------------

    private fun mimeFor(name: String) = when {
        name.endsWith(".mp3", true) -> "audio/mpeg"
        name.endsWith(".m4a", true) -> "audio/mp4"
        name.endsWith(".flac", true) -> "audio/flac"
        else -> "audio/wav"
    }

    private fun upload(base: String, bytes: ByteArray, filename: String) {
        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(240, TimeUnit.SECONDS)   // librosa + noise reduction is not fast
            .build()
        try {
            // Django's CsrfViewMiddleware wants the cookie AND a matching header.
            // Fetching "/" is the cheapest way for a native client to obtain both.
            val token = client.newCall(Request.Builder().url(base + "/").build()).execute().use { r ->
                r.headers("Set-Cookie").firstOrNull { it.startsWith("csrftoken=") }
                    ?.substringAfter("csrftoken=")?.substringBefore(";")
            }
            if (token == null) {
                status = "no csrftoken from " + base + " - is that the detection server?"
                return
            }

            val body = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart(
                    "audio", filename,
                    bytes.toRequestBody(mimeFor(filename).toMediaType())
                ).build()

            val req = Request.Builder().url(base + "/upload-audio/")
                .addHeader("Cookie", "csrftoken=" + token)
                .addHeader("X-CSRFToken", token)
                .addHeader("Referer", base + "/")
                .post(body).build()

            client.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    status = "HTTP " + resp.code
                    result = text.take(300)
                    return
                }
                val json = JSONObject(text)
                if (json.optString("status") != "success") {
                    status = "rejected by server"
                    result = json.optString("message")
                    return
                }
                status = "ANALYSED"
                result = listOf(
                    "Sample rate" to (json.optInt("sample_rate").toString() + " Hz"),
                    "Channels" to json.optString("channels"),
                    "Duration" to (json.optDouble("duration").toString() + " s"),
                    "After trim" to (json.optDouble("trimmed_duration").toString() + " s"),
                    "Segments" to (json.optInt("segment_count").toString() + " x " +
                            json.optInt("segment_duration") + "s"),
                    "Noise strength" to json.optDouble("noise_reduction_strength").toString()
                ).joinToString("\n") { it.first.padEnd(15) + it.second }
            }
        } catch (e: Exception) {
            status = "upload failed: " + e.message
        }
    }
}
