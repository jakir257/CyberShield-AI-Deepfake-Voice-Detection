package com.codephoton.callstream

import android.content.Context
import android.net.Uri
import android.os.Build
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Reads SMS messages and posts them to the detection server.
 *
 * The existing SCAN MESSAGES button keeps its original behaviour: it sends
 * the 10 most recent inbox SMS.  The SELECT MESSAGE flow can pass any
 * user-selected subset and sends those messages together in one request.
 */
object Messages {

    private const val HOW_MANY = 10

    @Volatile var busy = false
    @Volatile var status = "idle"
    @Volatile var summary = ""
    @Volatile var result = ""

    /** Newest inbox messages as (sender, body, date-seconds). */
    fun readInbox(ctx: Context, limit: Int = HOW_MANY): List<Triple<String, String, Long>> {
        val out = mutableListOf<Triple<String, String, Long>>()

        ctx.contentResolver.query(
            Uri.parse("content://sms/inbox"),
            arrayOf("address", "body", "date"),
            null, null, "date DESC"
        )?.use { c ->
            while (c.moveToNext() && out.size < limit) {
                out.add(
                    Triple(
                        c.getString(0) ?: "unknown",
                        c.getString(1) ?: "",
                        c.getLong(2) / 1000
                    )
                )
            }
        }
        return out
    }

    /** Existing one-click behaviour: analyse the 10 newest inbox messages. */
    fun scanAndSend(ctx: Context, baseUrl: String) {
        if (busy) return
        busy = true
        result = ""
        summary = ""
        status = "reading inbox..."

        Thread {
            try {
                val sms = readInbox(ctx, HOW_MANY)
                if (sms.isEmpty()) {
                    status = "no SMS found in the inbox"
                    return@Thread
                }
                sendMessages(baseUrl, sms)
            } catch (e: SecurityException) {
                status = "SMS permission denied"
            } catch (e: Exception) {
                status = "scan failed: " + (e.message ?: e.javaClass.simpleName)
            } finally {
                busy = false
            }
        }.start()
    }

    /** Analyse exactly the messages selected by the user, together. */
    fun scanAndSend(
        ctx: Context,
        baseUrl: String,
        selected: List<Triple<String, String, Long>>
    ) {
        if (busy) return
        busy = true
        result = ""
        summary = ""
        status = "sending selected messages..."

        Thread {
            try {
                if (selected.isEmpty()) {
                    status = "no messages selected"
                    return@Thread
                }
                sendMessages(baseUrl, selected)
            } catch (e: Exception) {
                status = "scan failed: " + (e.message ?: e.javaClass.simpleName)
            } finally {
                busy = false
            }
        }.start()
    }

    private fun sendMessages(baseUrl: String, sms: List<Triple<String, String, Long>>) {
        val arr = JSONArray()
        for ((sender, body, date) in sms) {
            arr.put(
                JSONObject()
                    .put("sender", sender)
                    .put("body", body)
                    .put("date", date)
            )
        }

        val payload = JSONObject()
            .put("device", Build.MANUFACTURER + " " + Build.MODEL)
            .put("messages", arr)

        status = "sending " + sms.size + " messages..."
        post(baseUrl.trimEnd('/'), payload)
    }

    private fun post(base: String, payload: JSONObject) {
        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .build()

        val req = Request.Builder()
            .url(base + "/messages/")
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()

            if (!resp.isSuccessful) {
                status = "HTTP " + resp.code
                result = text.take(300)
                return
            }

            val json = JSONObject(text)

            if (json.optString("status") != "success") {
                status = "rejected"
                result = json.optString("message")
                return
            }

            val analysed = json.optInt("analysed")
            val flagged = json.optInt("flagged")

            status = if (flagged > 0) "$flagged OF $analysed FLAGGED" else "ALL CLEAR"
            summary = "$analysed messages scanned"

            // show the risky ones on the phone, worst first (server already sorted)
            val msgs = json.optJSONArray("messages") ?: JSONArray()
            val lines = StringBuilder()

            for (i in 0 until msgs.length()) {
                val m = msgs.getJSONObject(i)
                if (m.optString("level") == "low") continue

                val reasons = m.optJSONArray("reasons") ?: JSONArray()
                val why = (0 until reasons.length())
                    .joinToString(", ") { reasons.getString(it) }

                lines.append(m.optString("level").uppercase())
                    .append("  ").append(m.optInt("score")).append("/100\n")
                    .append(m.optString("sender")).append("\n")
                    .append(m.optString("body").take(110)).append("\n")
                    .append("-> ").append(why).append("\n\n")
            }

            result = if (lines.isEmpty()) "Nothing suspicious in the selected messages."
                     else lines.toString().trimEnd()
        }
    }
}
