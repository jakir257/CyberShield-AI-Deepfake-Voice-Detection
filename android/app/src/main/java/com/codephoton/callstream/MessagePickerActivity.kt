package com.codephoton.callstream

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.text.DateFormat
import java.util.Date

/**
 * Multi-message selector backed by the phone's SMS inbox.
 *
 * Android does not expose a standard intent that opens an arbitrary SMS app
 * and returns multiple existing SMS selections to a third-party app. This
 * screen therefore reads the device SMS provider (with READ_SMS permission)
 * and gives the user a multi-select UI inside CallStream. All selected
 * messages are then sent together to the same /messages/ endpoint used by
 * SCAN MESSAGES.
 */
class MessagePickerActivity : Activity() {

    private object Palette {
        const val BG = 0xFF111827.toInt()
        const val CARD = 0xFF1E293B.toInt()
        const val BORDER = 0xFF334155.toInt()
        const val FIELD = 0xFF172131.toInt()
        const val TEXT = 0xFFE2E8F0.toInt()
        const val MUTED = 0xFF94A3B8.toInt()
        const val ACCENT = 0xFFEF4444.toInt()
    }

    private var density = 1f
    private val selected = linkedSetOf<Int>()
    private lateinit var countLabel: TextView
    private lateinit var sendButton: Button
    private var messages = emptyList<Triple<String, String, Long>>()

    private val prefs by lazy { getSharedPreferences("callstream", MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        density = resources.displayMetrics.density
        window.statusBarColor = Palette.BG

        if (checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED) {
            finish()
            return
        }

        messages = runCatching { Messages.readInbox(this, 100) }.getOrElse {
            Toast.makeText(this, "Could not read SMS messages", Toast.LENGTH_LONG).show()
            emptyList()
        }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Palette.BG)
            setPadding(dp(16), dp(18), dp(16), dp(18))
        }

        root.addView(TextView(this).apply {
            text = "SELECT MESSAGES"
            setTextColor(Palette.TEXT)
            textSize = 22f
            setTypeface(Typeface.DEFAULT_BOLD)
            setPadding(dp(4), dp(4), dp(4), dp(4))
        })

        root.addView(TextView(this).apply {
            text = "Select one or more messages to send together to the PC."
            setTextColor(Palette.MUTED)
            textSize = 13f
            setPadding(dp(4), 0, dp(4), dp(12))
        })

        val listContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(10), 0, dp(10))
        }

        val top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }

        val selectAll = Button(this).apply {
            text = "SELECT ALL"
            setTextColor(Color.WHITE)
            setTypeface(Typeface.DEFAULT_BOLD)
            stateListAnimator = null
            background = rounded(Palette.FIELD)
            setOnClickListener {
                selected.clear()
                selected.addAll(messages.indices)
                refreshChecks(listContainer)
                updateCount()
            }
        }
        top.addView(selectAll, LinearLayout.LayoutParams(0, dp(48), 1f))

        countLabel = TextView(this).apply {
            text = "0 selected"
            setTextColor(Palette.MUTED)
            textSize = 13f
            gravity = android.view.Gravity.CENTER
        }
        top.addView(countLabel, LinearLayout.LayoutParams(dp(120), dp(48)))
        root.addView(top, LinearLayout.LayoutParams(-1, dp(48)))

        val scroll = ScrollView(this).apply {
            addView(listContainer, ViewGroup.LayoutParams(-1, -2))
        }
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))

        if (messages.isEmpty()) {
            listContainer.addView(TextView(this).apply {
                text = "No SMS messages found."
                setTextColor(Palette.MUTED)
                textSize = 15f
                setPadding(dp(8), dp(20), dp(8), dp(20))
            })
        } else {
            messages.forEachIndexed { index, message ->
                listContainer.addView(messageRow(index, message))
            }
        }

        sendButton = Button(this).apply {
            text = "SEND SELECTED TO PC"
            setTextColor(Color.WHITE)
            textSize = 14f
            setTypeface(Typeface.DEFAULT_BOLD)
            stateListAnimator = null
            background = rounded(Palette.ACCENT)
            setOnClickListener { sendSelected() }
            isEnabled = false
        }
        root.addView(sendButton, LinearLayout.LayoutParams(-1, dp(54)))

        setContentView(root)
        updateCount()
    }

    private fun messageRow(index: Int, message: Triple<String, String, Long>): LinearLayout {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(7), dp(10), dp(7))
            background = rounded(Palette.CARD)
        }

        val check = CheckBox(this).apply {
            text = message.first
            setTextColor(Palette.TEXT)
            textSize = 14f
            setTypeface(Typeface.DEFAULT_BOLD)
            setPadding(0, 0, 0, 0)
            setOnCheckedChangeListener { _, checked ->
                if (checked) selected.add(index) else selected.remove(index)
                updateCount()
            }
            tag = index
        }
        row.addView(check, LinearLayout.LayoutParams(-1, -2))

        row.addView(TextView(this).apply {
            text = message.second
            setTextColor(Palette.TEXT)
            textSize = 13f
            setPadding(dp(48), 0, dp(4), dp(2))
        })

        row.addView(TextView(this).apply {
            text = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
                .format(Date(message.third * 1000L))
            setTextColor(Palette.MUTED)
            textSize = 11f
            setPadding(dp(48), 0, dp(4), 0)
        })

        val params = LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) }
        row.layoutParams = params
        return row
    }

    private fun refreshChecks(container: LinearLayout) {
        for (i in 0 until container.childCount) {
            val row = container.getChildAt(i) as? LinearLayout ?: continue
            val check = row.getChildAt(0) as? CheckBox ?: continue
            val index = check.tag as? Int ?: continue
            check.setOnCheckedChangeListener(null)
            check.isChecked = selected.contains(index)
            check.setOnCheckedChangeListener { _, checked ->
                if (checked) selected.add(index) else selected.remove(index)
                updateCount()
            }
        }
    }

    private fun updateCount() {
        val count = selected.size
        countLabel.text = "$count selected"
        sendButton.isEnabled = count > 0 && !Messages.busy
    }

    private fun sendSelected() {
        if (selected.isEmpty()) return
        val target = prefs.getString("analyze_url", "")?.trim().orEmpty()
        if (target.isEmpty()) {
            Toast.makeText(this, "Enter the PC detection server first", Toast.LENGTH_LONG).show()
            return
        }

        val chosen = selected.sorted().map { messages[it] }
        Messages.scanAndSend(this, target, chosen)
        Toast.makeText(this, "Sending ${chosen.size} selected messages to PC", Toast.LENGTH_SHORT).show()
        finish()
    }

    private fun rounded(color: Int) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = dp(10).toFloat()
        if (color == Palette.CARD || color == Palette.FIELD) {
            setStroke(dp(1), Palette.BORDER)
        }
    }

    private fun dp(v: Int) = (v * density).toInt()
}
