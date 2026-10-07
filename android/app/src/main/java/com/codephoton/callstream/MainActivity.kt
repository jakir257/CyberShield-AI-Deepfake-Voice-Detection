package com.codephoton.callstream

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.DocumentsContract
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * Palette lifted from the CyberShield dashboard's style.css so the phone and the
 * browser read as one product.
 */
private object Palette {
    const val BG = 0xFF111827.toInt()          // body background
    const val CARD = 0xFF1E293B.toInt()        // .glass-card
    const val BORDER = 0xFF334155.toInt()
    const val FIELD = 0xFF172131.toInt()
    const val TEXT = 0xFFE2E8F0.toInt()
    const val MUTED = 0xFF94A3B8.toInt()
    const val ACCENT = 0xFFEF4444.toInt()      // red-500
    const val OK = 0xFF2ECC71.toInt()
}

class MainActivity : Activity() {

    private val ui = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("callstream", MODE_PRIVATE) }
    private var density = 1f

    private lateinit var serverUrl: EditText
    private lateinit var connectionLabel: TextView
    private lateinit var folderLabel: TextView
    private lateinit var scanBtn: Button
    private lateinit var recordBtn: Button
    private lateinit var detectStatus: TextView
    private lateinit var detectFile: TextView
    private lateinit var detectResult: TextView

    private lateinit var scanSmsBtn: Button
    private lateinit var selectSmsBtn: Button
    private lateinit var smsStatus: TextView
    private lateinit var smsSummary: TextView
    private lateinit var smsResult: TextView

    private lateinit var wsUrl: EditText
    private lateinit var streamBtn: Button
    private lateinit var streamStatus: TextView
    private lateinit var transcript: TextView

    private var treeUri: Uri? = null

    companion object {
        private const val PICK_FOLDER = 42
        private const val PICK_AUDIO_FILE = 43
        private const val REQUEST_READ_SMS = 44

        private val AUDIO_MIME_TYPES = arrayOf(
            "audio/wav",
            "audio/x-wav",
            "audio/mpeg",
            "audio/mp4",
            "audio/flac",
            "audio/*"
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        density = resources.displayMetrics.density
        window.statusBarColor = Palette.BG

        prefs.getString("tree_uri", null)?.let { treeUri = Uri.parse(it) }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Palette.BG)
            setPadding(dp(16), dp(20), dp(16), dp(24))
            layoutParams = ViewGroup.LayoutParams(-1, -1)
        }

        root.addView(header())
        root.addView(detectionCard())
        root.addView(messageCard())
        root.addView(streamCard())

        setContentView(ScrollView(this).apply {
            setBackgroundColor(Palette.BG)
            addView(root)
        })

        val need = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) need += Manifest.permission.POST_NOTIFICATIONS
        val missing = need.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), 1)

        poll()
    }

    // ----- layout ------------------------------------------------------------

    private fun header(): View = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(4), 0, dp(4), dp(18))
        addView(TextView(this@MainActivity).apply {
            text = "CYBERSHIELD AI"
            setTextColor(Palette.ACCENT)
            textSize = 24f
            setTypeface(Typeface.DEFAULT_BOLD)
        })
        addView(TextView(this@MainActivity).apply {
            text = "DEEPFAKE VOICE DETECTION"
            setTextColor(Palette.MUTED)
            textSize = 11f
            letterSpacing = 0.18f
            setTypeface(Typeface.DEFAULT_BOLD)
        })
    }

    private fun detectionCard(): View {
        val card = card()
        card.addView(cardTitle("VOICE DETECTION"))
        card.addView(fieldLabel("Detection server"))

        serverUrl = field(prefs.getString("analyze_url", "http://192.168.1.4:8000")!!)
        card.addView(serverUrl)

        // whether this phone can actually reach that address, checked every 10s
        connectionLabel = TextView(this).apply {
            text = "checking connection..."
            setTextColor(Palette.MUTED)
            textSize = 12f
            setPadding(0, 6, 0, 6)
        }
        card.addView(connectionLabel)

        card.addView(ghostButton("TEST CONNECTION") { onTestConnection() },
                     marginParams(dp(6)))

        card.addView(fieldLabel("Recordings folder"))
        folderLabel = TextView(this).apply {
            text = folderText()
            setTextColor(Palette.TEXT)
            textSize = 13f
            setPadding(dp(12), dp(10), dp(12), dp(10))
            background = GradientDrawable().apply {
                setColor(Palette.FIELD)
                cornerRadius = dp(10).toFloat()
                setStroke(dp(1), Palette.BORDER)
            }
        }
        card.addView(folderLabel, marginParams(dp(6)))

        card.addView(ghostButton("CHOOSE FOLDER") { pickFolder() }, marginParams(dp(10)))

        scanBtn = solidButton("SCAN & DETECT") { onScan() }
        card.addView(scanBtn, marginParams(dp(8)))

        recordBtn = ghostButton("RECORD & ANALYSE") { onRecord() }
        card.addView(recordBtn, marginParams(dp(8)))

        card.addView(divider(), marginParams(dp(16)))

        detectStatus = TextView(this).apply {
            text = "idle"
            setTextColor(Palette.MUTED)
            textSize = 15f
            setTypeface(Typeface.DEFAULT_BOLD)
        }
        card.addView(detectStatus, marginParams(dp(12)))

        detectFile = TextView(this).apply {
            setTextColor(Palette.MUTED)
            textSize = 12f
        }
        card.addView(detectFile, marginParams(dp(4)))

        detectResult = TextView(this).apply {
            setTextColor(Palette.TEXT)
            textSize = 13f
            typeface = Typeface.MONOSPACE
            setLineSpacing(dp(4).toFloat(), 1f)
        }
        card.addView(detectResult, marginParams(dp(10)))
        return card
    }

    private fun messageCard(): View {
        val card = card()
        card.addView(cardTitle("SCAM MESSAGE DETECTION"))
        card.addView(TextView(this).apply {
            text = "Reads your 10 most recent inbox SMS and scores them on the server."
            setTextColor(Palette.MUTED)
            textSize = 12f
            setPadding(0, 0, 0, dp(4))
        })

        scanSmsBtn = solidButton("SCAN MESSAGES") { onScanSms() }
        card.addView(scanSmsBtn, marginParams(dp(12)))

        // Lets the user choose multiple existing SMS messages instead of
        // automatically taking the newest 10.  The button uses the same
        // dark/white style as CHOOSE FOLDER.
        selectSmsBtn = ghostButton("SELECT MESSAGE") { onSelectSms() }
        card.addView(selectSmsBtn, marginParams(dp(8)))

        smsStatus = TextView(this).apply {
            text = "idle"
            setTextColor(Palette.MUTED)
            textSize = 15f
            setTypeface(Typeface.DEFAULT_BOLD)
        }
        card.addView(smsStatus, marginParams(dp(14)))

        smsSummary = TextView(this).apply {
            setTextColor(Palette.MUTED)
            textSize = 12f
        }
        card.addView(smsSummary, marginParams(dp(4)))

        smsResult = TextView(this).apply {
            setTextColor(Palette.TEXT)
            textSize = 12f
            typeface = Typeface.MONOSPACE
            setLineSpacing(dp(3).toFloat(), 1f)
        }
        card.addView(smsResult, marginParams(dp(10)))
        return card
    }

    private fun streamCard(): View {
        val card = card()
        card.addView(cardTitle("LIVE TRANSCRIPTION"))
        card.addView(fieldLabel("Whisper stream server"))

        wsUrl = field(prefs.getString("url", "ws://192.168.1.4:8765/ws/audio")!!)
        card.addView(wsUrl)

        streamBtn = ghostButton("START STREAMING") { onStream() }
        card.addView(streamBtn, marginParams(dp(10)))

        streamStatus = TextView(this).apply {
            text = "idle"
            setTextColor(Palette.MUTED)
            textSize = 14f
        }
        card.addView(streamStatus, marginParams(dp(12)))

        transcript = TextView(this).apply {
            setTextColor(Palette.TEXT)
            textSize = 15f
        }
        card.addView(transcript, marginParams(dp(6)))
        return card
    }

    // ----- widget helpers ----------------------------------------------------

    private fun dp(v: Int) = (v * density).toInt()

    private fun marginParams(top: Int) = LinearLayout.LayoutParams(-1, -2).apply { topMargin = top }

    private fun card(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(18), dp(18), dp(18), dp(18))
        background = GradientDrawable().apply {
            setColor(Palette.CARD)
            cornerRadius = dp(16).toFloat()
            setStroke(dp(1), Palette.BORDER)
        }
        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(16) }
    }

    private fun cardTitle(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Palette.TEXT)
        textSize = 15f
        setTypeface(Typeface.DEFAULT_BOLD)
        letterSpacing = 0.08f
        setPadding(0, 0, 0, dp(14))
    }

    private fun fieldLabel(text: String) = TextView(this).apply {
        this.text = text.uppercase()
        setTextColor(Palette.MUTED)
        textSize = 10f
        letterSpacing = 0.12f
        setTypeface(Typeface.DEFAULT_BOLD)
        setPadding(0, dp(10), 0, dp(5))
    }

    private fun field(value: String) = EditText(this).apply {
        setText(value)
        inputType = InputType.TYPE_TEXT_VARIATION_URI
        setTextColor(Palette.TEXT)
        setHintTextColor(Palette.MUTED)
        textSize = 13f
        setPadding(dp(12), dp(10), dp(12), dp(10))
        background = GradientDrawable().apply {
            setColor(Palette.FIELD)
            cornerRadius = dp(10).toFloat()
            setStroke(dp(1), Palette.BORDER)
        }
    }

    private fun solidButton(text: String, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        setTextColor(Color.WHITE)
        textSize = 14f
        setTypeface(Typeface.DEFAULT_BOLD)
        isAllCaps = false
        stateListAnimator = null
        background = GradientDrawable().apply {
            setColor(Palette.ACCENT)
            cornerRadius = dp(10).toFloat()
        }
        setPadding(dp(16), dp(14), dp(16), dp(14))
        setOnClickListener { onClick() }
    }

    private fun ghostButton(text: String, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        setTextColor(Palette.TEXT)
        textSize = 13f
        setTypeface(Typeface.DEFAULT_BOLD)
        isAllCaps = false
        stateListAnimator = null
        background = GradientDrawable().apply {
            setColor(Palette.FIELD)
            cornerRadius = dp(10).toFloat()
            setStroke(dp(1), Palette.BORDER)
        }
        setPadding(dp(16), dp(13), dp(16), dp(13))
        setOnClickListener { onClick() }
    }

    private fun divider() = View(this).apply {
        setBackgroundColor(Palette.BORDER)
        layoutParams = LinearLayout.LayoutParams(-1, dp(1))
    }

    // ----- actions -----------------------------------------------------------

    private fun folderText(): String {
        val uri = treeUri ?: return "none selected"
        // tree URIs look like .../tree/primary%3ARecordings%2FCall - show the readable tail
        val decoded = Uri.decode(uri.toString())
        val tail = decoded.substringAfterLast(':')
        return if (tail.isEmpty()) "selected" else tail
    }

    private fun pickFolder() {
        startActivityForResult(
            Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), PICK_FOLDER
        )
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)

        if (resultCode != RESULT_OK) return

        if (requestCode == PICK_FOLDER) {
            val uri = data?.data ?: return

            // Keep the folder permission so the same folder can be opened
            // again after the app is restarted.
            runCatching {
                contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            }

            treeUri = uri
            prefs.edit().putString("tree_uri", uri.toString()).apply()
            folderLabel.text = folderText()
            Analyzer.status = "folder set - press SCAN & DETECT"
            return
        }

        if (requestCode == PICK_AUDIO_FILE) {
            val uri = data?.data ?: return

            // The picker grants read access to this exact selected file.
            // Do not search the folder: upload only this URI.
            runCatching {
                contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            }

            val target = serverUrl.text.toString().trim()
            if (target.isEmpty()) {
                Analyzer.status = "enter the PC address first"
                return
            }

            Analyzer.scanAndUpload(this, uri, target)
        }
    }

    /**
     * SCAN & DETECT now opens the already-selected recordings folder and asks
     * the user to choose exactly ONE audio file. Nothing is auto-selected.
     */
    private fun onScan() {
        val tree = treeUri
        if (tree == null) {
            Analyzer.status = "choose a recordings folder first"
            return
        }

        val target = serverUrl.text.toString().trim()
        if (target.isEmpty()) {
            Analyzer.status = "enter the PC address first"
            return
        }

        if (Analyzer.busy || Analyzer.recording) return

        prefs.edit().putString("analyze_url", target).apply()

        try {
            val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = "audio/*"
                putExtra(Intent.EXTRA_MIME_TYPES, AUDIO_MIME_TYPES)
                addFlags(
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or
                    Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
                )

                // Open the folder chosen with CHOOSE FOLDER.
                // Android 8+ supports EXTRA_INITIAL_URI.
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    putExtra(DocumentsContract.EXTRA_INITIAL_URI, tree)
                }
            }

            startActivityForResult(intent, PICK_AUDIO_FILE)

        } catch (e: Exception) {
            Analyzer.status = "could not open audio picker: " +
                    (e.message ?: e.javaClass.simpleName)
        }
    }

    /**
     * Check the typed address immediately instead of waiting up to 10s for the
     * next heartbeat. Also saves it, so the app comes back to a working
     * address next launch.
     */
    private fun onTestConnection() {
        val target = serverUrl.text.toString().trim()

        if (target.isEmpty()) {
            Analyzer.connectionStatus = "enter the PC address first"
            return
        }

        prefs.edit().putString("analyze_url", target).apply()
        Analyzer.connectionStatus = "testing $target ..."
        Analyzer.testNow(target, deviceName())
    }

    private fun onRecord() {
        val target = serverUrl.text.toString().trim()
        prefs.edit().putString("analyze_url", target).apply()
        if (Analyzer.recording) {
            Analyzer.stopAndUpload(target)
        } else {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
                return
            }
            // the URL goes in at start(), not at stop, because segments are
            // uploaded while the recording is still running
            Analyzer.start(target)
        }
    }

    private fun onScanSms() {
        if (checkSelfPermission(Manifest.permission.READ_SMS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.READ_SMS), 2)
            Messages.status = "grant SMS access, then press again"
            return
        }
        val target = serverUrl.text.toString().trim()
        if (target.isEmpty()) {
            Messages.status = "enter the PC address first"
            return
        }
        prefs.edit().putString("analyze_url", target).apply()
        Messages.scanAndSend(this, target)
    }

    private fun onSelectSms() {
        if (checkSelfPermission(Manifest.permission.READ_SMS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.READ_SMS), REQUEST_READ_SMS)
            Messages.status = "grant SMS access, then press SELECT MESSAGE again"
            return
        }

        val target = serverUrl.text.toString().trim()
        if (target.isEmpty()) {
            Messages.status = "enter the PC address first"
            return
        }

        prefs.edit().putString("analyze_url", target).apply()
        startActivity(Intent(this, MessagePickerActivity::class.java))
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_READ_SMS &&
            grantResults.isNotEmpty() &&
            grantResults[0] == PackageManager.PERMISSION_GRANTED
        ) {
            startActivity(Intent(this, MessagePickerActivity::class.java))
        }
    }

    private fun onStream() {
        if (StreamService.running) {
            stopService(Intent(this, StreamService::class.java))
        } else {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
                return
            }
            val target = wsUrl.text.toString().trim()
            prefs.edit().putString("url", target).apply()
            startForegroundService(
                Intent(this, StreamService::class.java).putExtra(StreamService.EXTRA_URL, target)
            )
        }
    }

    /** Same name the SMS push already reports, so the dashboard shows one
     *  device rather than two spellings of the same phone. */
    private fun deviceName() = Build.MANUFACTURER + " " + Build.MODEL

    /** Worker state lives in volatile fields; polling it is cheaper than binding. */
    private fun poll() {

        // the heartbeat follows whatever address is in the box, so editing it
        // shows the result within one beat instead of needing a restart
        Analyzer.startHeartbeat(serverUrl.text.toString().trim(), deviceName())

        connectionLabel.text = if (Analyzer.connected) {
            "PC connected"
        } else {
            "PC not connected - " + Analyzer.connectionStatus
        }
        connectionLabel.setTextColor(
            if (Analyzer.connected) Palette.OK else Palette.MUTED
        )

        detectStatus.text = Analyzer.status
        detectStatus.setTextColor(
            when {
                Analyzer.status == "ANALYSED" -> Palette.OK
                Analyzer.recording || Analyzer.busy -> Palette.ACCENT
                else -> Palette.MUTED
            }
        )
        detectFile.text = if (Analyzer.lastFile.isEmpty()) "" else "file: " + Analyzer.lastFile
        detectResult.text = Analyzer.result
        recordBtn.text = if (Analyzer.recording) "STOP & UPLOAD" else "RECORD & ANALYSE"
        scanBtn.isEnabled = !Analyzer.busy

        smsStatus.text = Messages.status
        smsStatus.setTextColor(
            when {
                Messages.status.contains("FLAGGED") -> Palette.ACCENT
                Messages.status == "ALL CLEAR" -> Palette.OK
                Messages.busy -> Palette.ACCENT
                else -> Palette.MUTED
            }
        )
        smsSummary.text = Messages.summary
        smsResult.text = Messages.result
        scanSmsBtn.isEnabled = !Messages.busy
        selectSmsBtn.isEnabled = !Messages.busy

        streamStatus.text = StreamService.status
        transcript.text = StreamService.lastTranscript
        streamBtn.text = if (StreamService.running) "STOP STREAMING" else "START STREAMING"

        ui.postDelayed({ poll() }, 400)
    }
}
