package com.codephoton.callstream

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit

class StreamService : Service() {

    companion object {
        const val EXTRA_URL = "url"
        const val NOTIF_CHANNEL = "callstream"

        /** Server channel 4 = MIC_TEST. The mic is the only source Android grants a
         *  non-system app; on speakerphone it captures both sides acoustically. */
        const val AUDIO_CHANNEL = 4
        const val SAMPLE_RATE = 16000
        const val FRAME_SAMPLES = 1600            // 100 ms
        const val HEADER_SIZE = 20

        @Volatile var running = false
        @Volatile var status = "idle"
        @Volatile var lastTranscript = ""
    }

    private var worker: Thread? = null
    private var socket: WebSocket? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (running) return START_NOT_STICKY
        val url = intent?.getStringExtra(EXTRA_URL).orEmpty()
        if (url.isEmpty()) {
            stopSelf()
            return START_NOT_STICKY
        }

        startForeground(1, notification())
        running = true
        status = "connecting…"
        lastTranscript = ""
        worker = Thread { stream(url) }.also { it.start() }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        running = false
        socket?.close(1000, "stopped")
        socket = null
        worker?.join(2000)
        status = "idle"
        super.onDestroy()
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(
                NotificationChannel(NOTIF_CHANNEL, "CallStream", NotificationManager.IMPORTANCE_LOW)
            )
        }
        return Notification.Builder(this, NOTIF_CHANNEL)
            .setContentTitle("CallStream")
            .setContentText("Streaming microphone audio")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build()
    }

    private fun stream(url: String) {
        val client = OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()

        client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                socket = ws
                status = "connected — streaming"
                ws.send(
                    JSONObject()
                        .put("type", "handshake")
                        .put("device_model", "${Build.MANUFACTURER} ${Build.MODEL}")
                        .put("sample_rate", SAMPLE_RATE)
                        .put("channels", "mic")
                        .toString()
                )
                Thread { capture(ws) }.start()
            }

            override fun onMessage(ws: WebSocket, text: String) {
                val json = runCatching { JSONObject(text) }.getOrNull() ?: return
                when (json.optString("type")) {
                    "ack" -> status = "handshake ok — streaming"
                    "transcription" -> lastTranscript = json.optString("transcription")
                }
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                status = "error: ${t.message}"
                running = false
            }

            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                status = "disconnected ($code)"
                running = false
            }
        })
    }

    private fun capture(ws: WebSocket) {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val recorder = try {
            AudioRecord(
                // MIC, not VOICE_COMMUNICATION: the latter runs echo cancellation, which on
                // speakerphone treats the caller's voice as echo and removes it.
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf, FRAME_SAMPLES * 2 * 4)
            )
        } catch (e: SecurityException) {
            status = "mic permission denied"
            running = false
            return
        }

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            status = "AudioRecord init failed"
            running = false
            recorder.release()
            return
        }

        val frame = ByteArray(FRAME_SAMPLES * 2)
        var seq = 0
        try {
            recorder.startRecording()
            while (running) {
                var off = 0
                while (off < frame.size && running) {
                    val n = recorder.read(frame, off, frame.size - off)
                    if (n <= 0) break
                    off += n
                }
                if (off <= 0) continue
                ws.send((header(seq++, off) + frame.copyOf(off)).toByteString())
            }
        } catch (e: Exception) {
            status = "capture error: ${e.message}"
        } finally {
            runCatching { recorder.stop() }
            recorder.release()
        }
    }

    /** Matches the server's ">2sBBQII": magic, channel, encoding, ts_ms, seq, payload_len. */
    private fun header(seq: Int, payloadLen: Int): ByteArray =
        ByteBuffer.allocate(HEADER_SIZE).order(ByteOrder.BIG_ENDIAN).apply {
            put('C'.code.toByte())
            put('S'.code.toByte())
            put(AUDIO_CHANNEL.toByte())
            put(0)                              // encoding 0 = raw PCM16
            putLong(System.currentTimeMillis())
            putInt(seq)
            putInt(payloadLen)
        }.array()
}
