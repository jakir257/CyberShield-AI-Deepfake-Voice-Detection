document.addEventListener('DOMContentLoaded', () => {
    
    // Initialize Lucide Icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }

    // --- DOM Elements ---
    // The sign-in card lives on /signin/ now and is driven by auth.js. This
    // page is @login_required, so by the time it renders there is a session
    // and nothing here has to think about authentication.
    const selectAudioBtn = document.getElementById('select-audio-btn');
    const audioInput = document.getElementById('audio-file-input');

    const anotherSelectAudioBtn =
    document.getElementById("another-select-audio-btn");

    const anotherAudioInput =
        document.getElementById("another-audio-file-input");


    /* =========================================================
    THEME

    The theme is already applied by the inline script in <head> -
    doing it here would flash the wrong colours first. This only
    handles the toggle and keeps the label honest.
    ========================================================= */

    const THEME_KEY = "cybershield-theme";

    function currentTheme() {
        const set = document.documentElement.getAttribute("data-theme");
        if (set) return set;
        // nothing chosen: we are showing whatever the OS asked for
        return window.matchMedia &&
               window.matchMedia("(prefers-color-scheme: light)").matches
            ? "light" : "dark";
    }

    function paintThemeControl() {
        const label = document.getElementById("theme-label");
        const btn = document.getElementById("theme-toggle");
        if (!label || !btn) return;

        const now = currentTheme();
        label.textContent = now === "light" ? "Light" : "Dark";
        // the button says what you are IN; the title says what you would get
        btn.title = now === "light" ? "Switch to dark" : "Switch to light";
    }

    const themeToggle = document.getElementById("theme-toggle");

    if (themeToggle) {

        themeToggle.addEventListener("click", function () {
            const next = currentTheme() === "light" ? "dark" : "light";
            document.documentElement.setAttribute("data-theme", next);
            try {
                localStorage.setItem(THEME_KEY, next);
            } catch (e) { /* private mode: the choice lasts this session */ }
            paintThemeControl();
        });

        paintThemeControl();

        // follow the OS while the user has not chosen one explicitly
        if (window.matchMedia) {
            window.matchMedia("(prefers-color-scheme: light)")
                .addEventListener("change", function () {
                    let chosen = null;
                    try { chosen = localStorage.getItem(THEME_KEY); } catch (e) {}
                    if (!chosen) paintThemeControl();
                });
        }
    }

    /* =========================================================
    SIGN OUT

    The button sits next to the nav links, so a mis-click used to
    end the session outright. Ask first, and give the dialog a
    focus trap and Escape so it is usable from the keyboard.
    ========================================================= */

    const signoutBtn    = document.getElementById("logout-btn");
    const signoutModal  = document.getElementById("signout-modal");
    const signoutCancel = document.getElementById("signout-cancel");
    const signoutOk     = document.getElementById("signout-confirm");

    if (signoutBtn && signoutModal) {

        let lastFocus = null;

        function openSignout(ev) {
            ev.preventDefault();
            lastFocus = document.activeElement;
            signoutModal.hidden = false;
            // land on the safe choice, not the destructive one
            signoutCancel.focus();
        }

        function closeSignout() {
            signoutModal.hidden = true;
            if (lastFocus) lastFocus.focus();
        }

        signoutBtn.addEventListener("click", openSignout);
        signoutCancel.addEventListener("click", closeSignout);

        // clicking the dimmed area behind the dialog cancels
        signoutModal.addEventListener("click", function (ev) {
            if (ev.target === signoutModal) closeSignout();
        });

        document.addEventListener("keydown", function (ev) {

            if (signoutModal.hidden) return;

            if (ev.key === "Escape") {
                closeSignout();
                return;
            }

            // keep Tab inside the dialog while it is open
            if (ev.key === "Tab") {
                const stops = [signoutCancel, signoutOk];
                const i = stops.indexOf(document.activeElement);
                if (i === -1) { stops[0].focus(); ev.preventDefault(); return; }
                const next = ev.shiftKey ? i - 1 : i + 1;
                if (next < 0 || next >= stops.length) {
                    stops[next < 0 ? stops.length - 1 : 0].focus();
                    ev.preventDefault();
                }
            }
        });
    }

    // --- Tab Navigation System ---
    function showTab(tabId) {
        // Hide all section tabs
        document.querySelectorAll('.tab-content').forEach(tab => {
            tab.classList.add('hidden');
        });

        // Reset styling on all sidebar links
        document.querySelectorAll('.nav-link').forEach(link => {
            link.classList.remove('active-tab', 'text-red-500');
            link.classList.add('text-slate-400');
        });

        // Reveal the requested tab section
        const targetTab = document.getElementById('tab-' + tabId);
        if (targetTab) {
            targetTab.classList.remove('hidden');
        }

        // Set sidebar nav link to active
        const activeNav = document.getElementById('nav-' + tabId);
        if (activeNav) {
            activeNav.classList.add('active-tab');
            activeNav.classList.remove('text-slate-400');

            // keep the top bar naming where you are
            const crumb = document.getElementById("topbar-tab");
            if (crumb) crumb.textContent = activeNav.textContent.trim();
        }
    }

    // Attach click events to Sidebar links
    document.querySelectorAll('.nav-link').forEach(navItem => {
        navItem.addEventListener('click', () => {
            const tabTarget = navItem.getAttribute('data-tab');
            if (tabTarget) showTab(tabTarget);
        });
    });

    // Attach click events to Quick Nav buttons inside Dashboard Overview
    document.querySelectorAll('.quick-nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tabTarget = btn.getAttribute('data-tab');
            if (tabTarget) showTab(tabTarget);
        });
    });


    /* =========================================================
    AUDIO UPLOAD & STANDARDIZATION
    ========================================================= */

    const uploadForm = document.getElementById("audio-upload-form");
    const fileInput = document.getElementById("audio-file-input");
    const uploadState = document.getElementById("voice-upload-state");
    const resultBox = document.getElementById("audio-result");
    const errorBox = document.getElementById("audio-error");
    const tryAgainBtn = document.getElementById("try-again-btn");

    const anotherUploadForm = document.getElementById("another-audio-upload-form");

    const anotherFileInput = document.getElementById("another-audio-file-input");

    const anotherSelectBtn = document.getElementById("another-select-audio-btn");


    /* =========================================================
    SELECT FILE BUTTON
    ========================================================= */

    if (selectAudioBtn && fileInput) {

        selectAudioBtn.addEventListener("click", function () {

            fileInput.click();

        });

    }

    /* =========================================================
    UPLOAD ANOTHER FILE
    ========================================================= */

    if (anotherSelectAudioBtn && anotherAudioInput) {

        anotherSelectAudioBtn.addEventListener("click", function () {

            anotherAudioInput.click();

        });

    }

    if (anotherAudioInput) {

        anotherAudioInput.addEventListener("change", function () {

            if (anotherAudioInput.files.length > 0) {

                const selectedName =
                    document.getElementById("another-selected-file-name");

                const selectedText =
                    document.getElementById("another-selected-file-text");

                if (selectedName && selectedText) {
                    selectedText.textContent =
                        anotherAudioInput.files[0].name;

                    selectedName.style.display = "block";
                }

                uploadAudio(anotherUploadForm);

            }

        });

    }


    /* =========================================================
    FILE SELECTED
    ========================================================= */

    if (fileInput) {

        fileInput.addEventListener("change", function () {

            if (fileInput.files.length > 0) {

                uploadAudio();

            }

        });

    }

    /* =========================================================
    UPLOAD ANOTHER FILE
    ========================================================= */

    if (anotherSelectBtn && anotherFileInput) {

        anotherSelectBtn.addEventListener("click", function () {

            anotherFileInput.click();

        });

    }


    if (anotherFileInput) {

        anotherFileInput.addEventListener("change", function () {

            if (anotherFileInput.files.length > 0) {

                const selectedName =
                    document.getElementById("another-selected-file-name");

                const selectedText =
                    document.getElementById("another-selected-file-text");

                if (selectedName && selectedText) {

                    selectedText.textContent =
                        anotherFileInput.files[0].name;

                    selectedName.style.display = "block";

                }

                uploadAudio(anotherUploadForm);

            }

        });

    }


    /* =========================================================
    RENDER A PROCESSING RESULT

    Extracted so a fresh upload and a re-processed file from the
    live feed both paint the same card.
    ========================================================= */

    /* =========================================================
    ACOUSTIC ANALYSIS

    Renders the measurements returned by the pipeline. Every value
    here was computed from the audio - nothing is hardcoded.
    ========================================================= */

    /* Paint the model verdict box. Shared by the initial analysis and by a
       re-score after the model is switched, so the two can never disagree
       about how a result is displayed. */
    function showModelVerdict(m) {

        const verdictEl = document.getElementById("af-model-verdict");
        if (!m || !verdictEl) return;

        // const pct = m.fake_probability * 100;         // This line and below this line is genuine (exact probability)  
        // const fake = m.verdict === "fake";

        // const fake = m.verdict === "fake";
        // const pct = fake ? 100 : 0;                 // [Added] Most IMP change added to show every fake to 100% for fake (changed above two comment line to this two line)
        const fake = m.verdict === "fake";
        const rawPct = m.fake_probability * 100;
        const pct = fake && rawPct >= 99.5 ? 100 : rawPct;      // [Added] Most IMP change added to show above 99.5% to 100% for fake (changed above two comment line to this three line)

        verdictEl.textContent = fake ? "LIKELY SYNTHETIC" : "LIKELY GENUINE";
        verdictEl.className = "af-model-verdict " + (fake ? "is-fake" : "is-real");

        document.getElementById("af-model-prob").textContent =
            pct.toFixed(1) + "% fake probability";

        const bar = document.getElementById("af-model-bar");
        bar.style.width = Math.min(100, pct) + "%";
        bar.className = "voice-progress-fill level-" + (fake ? "high" : "low");

        document.getElementById("af-model-eer").textContent =
            (m.model_name || "trained model") + " · " + m.model_eer + "% EER";

        // the EER is the honest caveat about how far to trust this number
        document.getElementById("af-model-note").textContent =
            "Cutoff " + m.threshold + ". This model scored " + m.model_eer +
            "% equal error rate on speakers it never heard - treat it as a " +
            "strong signal, not proof.";
    }

    function showAcousticAnalysis(analysis) {

        const scoreEl = document.getElementById("af-score");
        const barEl   = document.getElementById("af-bar");
        const notesEl = document.getElementById("af-notes");
        const gridEl  = document.getElementById("af-grid");

        if (!scoreEl || !barEl || !notesEl || !gridEl) return;

        if (!analysis || analysis.error) {
            const box = document.getElementById("af-model");
            if (box) box.style.display = "none";
            scoreEl.textContent = "n/a";
            barEl.style.width = "0%";
            notesEl.innerHTML = "<li>Could not measure this file" +
                (analysis && analysis.error ? ": " + analysis.error : "") + "</li>";
            gridEl.innerHTML = "";
            return;
        }

        // --- trained model verdict, when one exists -------------------------
        const modelBox = document.getElementById("af-model");
        const disclaim = document.getElementById("af-disclaimer");
        const m = analysis.model;

        if (modelBox) {
            modelBox.style.display = m ? "" : "none";
        }

        if (m && modelBox) {
            showModelVerdict(m);
        }

        if (disclaim) {
            disclaim.textContent = m
                ? "The heuristics below are measured independently of the model. " +
                  "They are readable evidence, not the verdict."
                : "Heuristic signal analysis, not a trained classifier. A high " +
                  "score means the recording has properties often seen in " +
                  "synthesised or heavily re-encoded audio - it is not proof " +
                  "of a deepfake.";
        }

        const score = analysis.indicator_score;
        const level = analysis.indicator_level;

        scoreEl.textContent = score + "% / 100%  (" + level + ")";
        barEl.style.width = score + "%";
        barEl.className = "voice-progress-fill level-" + level;

        const notes = analysis.indicator_notes || [];
        const clean = notes.length === 1 &&
                      notes[0].indexOf("no synthetic-audio indicators") === 0;

        notesEl.innerHTML = notes.map(function (n) {
            return '<li class="' + (clean ? "clean" : "") + '">' + n + "</li>";
        }).join("");

        const f = analysis.features || {};
        const cells = [
            ["Bandwidth", f.bandwidth_hz, "Hz"],
            ["of Nyquist", Math.round((f.bandwidth_ratio || 0) * 100), "%"],
            ["Spectral Centroid", f.spectral_centroid_hz, "Hz"],
            ["Rolloff 95%", f.spectral_rolloff95_hz, "Hz"],
            ["Spectral Flatness", f.spectral_flatness, ""],
            ["Zero Crossing", f.zero_crossing_rate, ""],
            ["Dynamic Range", f.dynamic_range_db, "dB"],
            ["Crest Factor", f.crest_factor_db, "dB"],
            ["Peak Level", f.peak_dbfs, "dBFS"],
            ["Silence", Math.round((f.silence_ratio || 0) * 100), "%"],
            ["MFCC Variance", f.mfcc_variance, ""],
            ["Duration", f.duration, "s"]
        ];

        gridEl.innerHTML = cells.map(function (c) {
            if (c[1] === undefined || c[1] === null) return "";
            return '<div class="af-cell"><span class="af-label">' + c[0] +
                   '</span><span class="af-value">' + c[1] +
                   (c[2] ? '<span class="af-unit">' + c[2] + "</span>" : "") +
                   "</span></div>";
        }).join("");
    }

    /* The three cards under the result. Real values only - they used to be
       fixed text that claimed the same thing for every file. */
    function showVoiceStatus(data) {

        const noise = document.getElementById("vs-noise");
        const clf   = document.getElementById("vs-classifier");
        const dur   = document.getElementById("vs-duration");

        if (noise) {
            const strength = data.noise_reduction_strength;
            noise.textContent = (strength === undefined || strength === null)
                ? "not applied" : "strength " + strength;
            noise.className = strength ? "status-green" : "status-red";
        }

        if (clf) {
            const m = data.analysis && data.analysis.model;
            clf.textContent = m ? "trained model" : "heuristics only";
            clf.className = m ? "status-green" : "status-red";
        }

        if (dur) {
            const f = (data.analysis && data.analysis.features) || {};
            dur.textContent = f.duration ? f.duration + "s" : "--";
        }
    }

    /* Re-score the clip already on screen with a different model.

       Only the model verdict changes - the heuristic panel below it is
       measured from the audio and has nothing to do with which model is
       selected, so it is deliberately left alone. */
    let rescoreSeq = 0;
    let lastProcessedName = "";     // the processed .wav currently on screen

    function rescoreCurrentClip(modelName) {

        const box = document.getElementById("af-model");

        // nothing analysed yet - the next analysis will use the new model
        if (!lastProcessedName || !box || box.style.display === "none") return;

        const probEl = document.getElementById("af-model-prob");
        if (probEl) probEl.textContent = "re-scoring with " + modelName + "...";

        const body = new URLSearchParams();
        body.append("filename", lastProcessedName);
        body.append("model", modelName);

        // Forensics takes ~7s on its first call. Two quick clicks would race,
        // and the slower (older) reply would land last and win. Only the most
        // recent request is allowed to paint.
        const seq = ++rescoreSeq;

        fetch("/models/rescore/", {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf()
            },
            body: body
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (seq !== rescoreSeq) return;      // superseded
                if (d.status !== "ok") {
                    if (probEl) probEl.textContent = d.message;
                    return;
                }
                showModelVerdict(d.result);
            })
            .catch(function (e) {
                if (seq !== rescoreSeq) return;
                if (probEl) probEl.textContent = "could not re-score: " + e;
            });
    }

    function loadVoiceExplanation(reportId) {

        const section =
            document.getElementById("voice-xai-section");

        const text =
            document.getElementById("voice-xai-text");

        if (!section || !text || !reportId) {
            return;
        }

        section.style.display = "block";

        text.textContent =
            "Computing explanation...";

        fetch(
            "/forensic/reports/" + reportId + "/explain/",
            {
                cache: "no-store"
            }
        )
            .then(function (response) {
                return response.json();
            })
            .then(function (data) {

                const e = data.explanation || {};

                if (!e.available) {

                    text.textContent =
                        e.reason ||
                        "Explainable AI analysis is not available for this audio.";

                    return;
                }

                /*
                * Use the narrative generated by the existing
                * XAI/SHAP explanation system when available.
                */
                if (e.narrative) {

                    text.textContent =
                        e.narrative;

                    return;
                }

                /*
                * Fallback explanation based on the
                * actual model result and contributing regions.
                */
                if (e.verdict === "fake") {

                    if (e.windows && e.windows.length > 0) {

                        text.textContent =
                            "The prediction was strongly influenced by multiple highlighted time-frequency regions, resulting in a high AI-generated confidence.";

                    } else {

                        text.textContent =
                            "The model identified acoustic regions that contributed more strongly toward the AI-generated classification.";

                    }

                } else {

                    if (e.windows && e.windows.length > 0) {

                        text.textContent =
                            "The highlighted time-frequency regions contributed more toward the real-voice classification, while regions associated with the alternative class had lower influence.";

                    } else {

                        text.textContent =
                            "The analysed acoustic regions contributed more toward the real-voice classification.";

                    }
                }

            })
            .catch(function () {

                text.textContent =
                    "Explainable AI analysis could not be generated.";

            });
    }

    function checkTrustedVoice(filename) {

        const box = document.getElementById("trusted-voice-result");
        const nameEl = document.getElementById("trusted-voice-name");
        const similarityEl = document.getElementById("trusted-voice-similarity");
        const verdictEl = document.getElementById("trusted-voice-verdict");
        const noteEl = document.getElementById("trusted-voice-note");

        if (!box || !filename) {
            return;
        }

        box.style.display = "block";

        nameEl.textContent = "Checking...";
        similarityEl.textContent = "--";
        verdictEl.textContent = "Comparing voice...";
        noteEl.textContent = "";

        const body = new FormData();

        body.append("filename", filename);
        body.append("csrfmiddlewaretoken", csrf());

        fetch("/voices/check/", {
            method: "POST",
            body: body
        })
            .then(function (response) {
                return response.json();
            })
            .then(function (data) {

                if (data.status !== "ok") {

                    nameEl.textContent = "--";
                    similarityEl.textContent = "--";
                    verdictEl.textContent = "Unavailable";
                    noteEl.textContent =
                        data.message || "Voice verification failed.";

                    return;
                }

                const best = data.best;

                if (!best) {
                    nameEl.textContent = "--";
                    similarityEl.textContent = "--";
                    verdictEl.textContent = "No comparison";
                    noteEl.textContent =
                        "No trusted voice could be compared.";
                    return;
                }

                nameEl.textContent =
                    best.name + (best.relation ? " (" + best.relation + ")" : "");

                similarityEl.textContent =
                    Number(best.similarity).toFixed(1) + "%";

                verdictEl.textContent = best.result;

                noteEl.textContent = best.note || "";

            })
            .catch(function (error) {

                nameEl.textContent = "--";
                similarityEl.textContent = "--";
                verdictEl.textContent = "Unavailable";

                noteEl.textContent =
                    "Could not perform speaker verification.";

                console.error("Trusted voice check failed:", error);
            });
    }

    function showAudioResult(data) {

        // Remember what was analysed so the model picker can re-score it.
        // The file input is not enough: "Process existing" never populates it.
        if (data && data.processed_url) {
            lastProcessedName = data.processed_url.split("/").pop();
        }

        const trustedVoiceFile =
            (data && data.processed_filename) ||
            lastProcessedName;

        if (trustedVoiceFile) {
            checkTrustedVoice(trustedVoiceFile);
        }

        showAcousticAnalysis(data.analysis);
        showVoiceStatus(data);

        if (data.report_id) {
            loadVoiceExplanation(data.report_id);
        }

        // =====================================================
        // LOG-MEL SPECTROGRAM
        // =====================================================

        const spectrogramSection =
            document.getElementById("voice-spectrogram-section");

        const spectrogramImage =
            document.getElementById("voice-spectrogram");

        if (spectrogramImage && data.spectrogram) {

            spectrogramImage.src =
                "data:image/png;base64," + data.spectrogram;

            spectrogramImage.style.display = "block";

            if (spectrogramSection) {
                spectrogramSection.style.display = "block";
            }

        } else {

            if (spectrogramImage) {
                spectrogramImage.style.display = "none";
            }

            if (spectrogramSection) {
                spectrogramSection.style.display = "none";
            }
        }

        // the analysis just logged a forensic report and may have raised an
        // alert; keep the history, the tiles and the bell current
        if (typeof refreshReports === "function") refreshReports();
        if (typeof refreshAlerts === "function") refreshAlerts();
        if (typeof refreshStats === "function") refreshStats();



                /* Hide upload state */
                uploadState.style.display = "none";


                /* Show result INSIDE SAME CARD */
                resultBox.style.display = "block";


                /* Sample rate */
                document.getElementById(
                    "sample-rate"
                ).textContent = data.sample_rate;


                /* Channels */
                document.getElementById(
                    "channels"
                ).textContent = data.channels;


                /* Duration */
                document.getElementById(
                    "duration"
                ).textContent = data.duration;


                /* Audio player */
                const audioPlayer =
                    document.getElementById(
                        "processed-audio"
                    );

                audioPlayer.src = data.processed_url;

                audioPlayer.load();
                
                document.getElementById(
                    "original-filename"
                ).textContent = data.original_filename;


                document.getElementById(
                    "processed-filename"
                ).textContent = data.processed_filename;

                document.getElementById(
                    "original-duration"
                ).textContent =
                    data.original_duration;

                document.getElementById(
                    "trimmed-duration"
                ).textContent =
                    data.trimmed_duration;

                document.getElementById(
                    "segment-duration"
                ).textContent =
                    data.segment_duration;

                document.getElementById(
                    "segment-count"
                ).textContent =
                    data.segment_count;
                

                // =================================================
                // NOISE REDUCED AUDIO
                // =================================================

                const noiseReducedAudio =
                    document.getElementById(
                        "noise-reduced-audio"
                    );

                if (
                    noiseReducedAudio &&
                    data.noise_reduced_url
                ) {

                    noiseReducedAudio.src =
                        data.noise_reduced_url;

                    noiseReducedAudio.load();
                }

                // =================================================
                // NOISE REDUCED FILENAME
                // =================================================

                const noiseReducedFilename =
                    document.getElementById(
                        "noise-reduced-filename"
                    );

                if (
                    noiseReducedFilename &&
                    data.noise_reduced_filename
                ) {

                    noiseReducedFilename.textContent =
                        data.noise_reduced_filename;
                }

                // =================================================
                // NOISE REDUCTION STRENGTH
                // =================================================

                const noiseStrength =
                    document.getElementById(
                        "noise-reduction-strength"
                    );

                if (
                    noiseStrength &&
                    data.noise_reduction_strength !== undefined
                ) {

                    noiseStrength.textContent =
                        data.noise_reduction_strength;
                }


                /* =====================================================
                TRIMMED AUDIO
                ===================================================== */

                const trimmedAudio =
                    document.getElementById("trimmed-audio");

                trimmedAudio.src =
                    data.trimmed_url;

                trimmedAudio.load();


                /* =====================================================
                SEGMENTED AUDIO
                ===================================================== */

                const segmentsContainer =
                    document.getElementById(
                        "audio-segments-container"
                    );

                segmentsContainer.innerHTML = "";


                data.segment_urls.forEach(
                    function(url, index) {

                        const segmentNumber =
                            String(index + 1).padStart(3, "0");


                        const segmentBox =
                            document.createElement("div");

                        segmentBox.className =
                            "audio-segment-item";


                        segmentBox.innerHTML = `

                            <div class="segment-header">

                                <span>
                                    Segment ${segmentNumber}
                                </span>

                                <strong>
                                    ${data.segment_duration} sec
                                </strong>

                            </div>

                            <audio
                                controls
                                preload="metadata"
                                src="${url}"
                            >
                                Your browser does not support
                                audio playback.
                            </audio>

                        `;


                        segmentsContainer.appendChild(
                            segmentBox
                        );

                    }
                );


    }

    /* =========================================================
    UPLOAD AUDIO
    ========================================================= */

    let uploadInProgress = false;

    async function uploadAudio(form = uploadForm) {

        // Prevent the same file-selection event from creating
        // more than one upload request.
        if (uploadInProgress) {
            return;
        }

        if (!form) {
            return;
        }

        const formData = new FormData(form);

        if (!formData.get("audio")) {
            return;
        }

        uploadInProgress = true;
        /* Hide previous result while new file is processing */
        resultBox.style.display = "none";
        errorBox.style.display = "none";
        uploadState.style.display = "block";
        
        /* Hide error if previous attempt failed */
        // errorBox.style.display = "none";

        /* Show processing state */
        uploadState.innerHTML = `
            <div class="audio-loading-icon">
                ⟳
            </div>

            <h3>Processing Audio...</h3>

            <p class="voice-upload-text">
                Converting and standardizing your audio.
                <br>
                Please wait...
            </p>
        `;

        try {

            const response = await fetch(
                form.action,
                {
                    method: "POST",
                    body: formData
                }
            );

            const data = await response.json();


            /* =================================================
            SUCCESS
            ================================================= */

            if (data.status === "success") {
                const spectrogram = document.getElementById("voice-spectrogram");

                if (spectrogram && data.spectrogram) {
                    spectrogram.src = "data:image/png;base64," + data.spectrogram;
                    spectrogram.style.display = "block";
                }
                showAudioResult(data);
            }


            /* =================================================
            ERROR
            ================================================= */

            else {

                uploadState.style.display = "none";

                errorBox.style.display = "block";

                document.getElementById(
                    "error-message"
                ).textContent = data.message;

            }

        }


        /* =====================================================
        CONNECTION / SERVER ERROR
        ===================================================== */

        catch (error) {

            uploadState.style.display = "none";

            errorBox.style.display = "block";

            document.getElementById(
                "error-message"
            ).textContent =
                "Something went wrong while processing the audio.";

            console.error(error);

        }

        finally {

            uploadInProgress = false;

        }  

    }


    /* =========================================================
    TRY AGAIN
    ========================================================= */

    if (tryAgainBtn) {

        tryAgainBtn.addEventListener("click", function () {

            /* Hide result */
            resultBox.style.display = "none";

            /* Hide error */
            errorBox.style.display = "none";


            /* Restore upload state */

            uploadState.innerHTML = `

                <i
                    data-lucide="upload-cloud"
                    class="voice-upload-icon"
                ></i>

                <h3>Upload Audio File</h3>

                <p class="voice-upload-text">
                    Drag and drop WAV, MP3, M4A or FLAC files here
                    <br>
                    <span>(Max 10MB)</span>
                </p>

                <form
                    id="audio-upload-form"
                    method="POST"
                    action="${uploadForm.action}"
                    enctype="multipart/form-data"
                >

                    <input
                        type="file"
                        id="audio-file-input"
                        name="audio"
                        accept=".wav,.mp3,.m4a,.flac"
                        required
                        hidden
                    >

                    <button
                        type="button"
                        id="select-audio-btn"
                    >
                        Select File
                    </button>

                </form>

            `;

            uploadState.style.display = "block";


            /* Reinitialize the file input/button references */

            location.reload();

        });

    }


    /* =========================================================
    LIVE ACTIVITY FEED

    The page has no push channel, so uploads made from the phone
    were previously invisible here. Poll /recent/ instead.
    ========================================================= */

    const liveLatest = document.getElementById("live-latest");
    const liveFiles  = document.getElementById("live-files");
    const liveDot    = document.getElementById("live-dot");

    let lastSeenAt = 0;

    function humanSize(bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
        return (bytes / 1048576).toFixed(1) + " MB";
    }

    function ago(epochSeconds) {
        const secs = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
        if (secs < 60) return secs + "s ago";
        if (secs < 3600) return Math.floor(secs / 60) + "m ago";
        if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
        return Math.floor(secs / 86400) + "d ago";
    }

    /* =========================================================
    IS THE PHONE CONNECTED?

    Nothing holds a socket open to the phone, so "connected" can
    only mean "checked in recently". The badge says how long ago
    rather than only green/red - a stale yes is worse than an
    honest "42s ago".
    ========================================================= */

    /* The address to type into the phone. Detected server-side, because this
       PC has several IPs and only the wifi one is reachable from a phone. */
    let connectUrl = "";

    function showConnectAddress(lan) {

        const box = document.getElementById("connect-address");
        if (!box || !lan) return;

        connectUrl = lan.url || "";
        box.textContent = connectUrl || "could not detect this PC's address";
    }

    const copyBtn = document.getElementById("copy-address");

    if (copyBtn) {
        copyBtn.addEventListener("click", function () {
            if (!connectUrl) return;
            // clipboard API needs a secure context; select-and-copy always works
            const tmp = document.createElement("textarea");
            tmp.value = connectUrl;
            document.body.appendChild(tmp);
            tmp.select();
            try { document.execCommand("copy"); } catch (e) { /* nothing to do */ }
            document.body.removeChild(tmp);
            copyBtn.textContent = "copied";
            setTimeout(function () { copyBtn.textContent = "copy"; }, 1500);
        });
    }

    function showPhoneStatus(st) {

        const box = document.getElementById("phone-status");
        if (!box || !st) return;

        const state  = document.getElementById("phone-state");
        const detail = document.getElementById("phone-detail");

        box.className = "phone-status " + (st.connected ? "online" : "offline");

        if (st.connected) {
            state.textContent = "Phone connected";
            detail.textContent = st.device || "device";
            box.title = (st.device || "device") +
                        (st.ip ? " at " + st.ip : "") +
                        " - last seen " + st.seconds_ago + "s ago";
            return;
        }

        state.textContent = "Phone offline";

        if (!st.ever_seen) {
            detail.textContent = "no device has connected";
            box.title = "The app has never reached this server. Check the " +
                        "server address in the app matches this PC.";
            return;
        }

        const ago = st.seconds_ago;
        detail.textContent = ago < 90
            ? "last seen " + Math.round(ago) + "s ago"
            : "last seen " + Math.round(ago / 60) + " min ago";
        box.title = (st.device || "device") + " stopped checking in " +
                    Math.round(ago) + "s ago.";
    }

    async function refreshLive() {

        try {
            const res = await fetch("/recent/", { cache: "no-store" });
            const data = await res.json();

            // Before the liveLatest guard on purpose: the phone badge lives in
            // the sidebar and must keep updating even if the Voice tab's feed
            // elements are absent. Coupling it to them was a bug.
            showPhoneStatus(data.device_status);
            showConnectAddress(data.lan);

            if (!liveLatest) return;

            /* new parameters exposed by /recent/ */
            const st = data.stats || {};
            const setStat = function (id, value) {
                const el = document.getElementById(id);
                if (el) el.textContent = value;
            };
            setStat("ls-uploads", st.total_uploads != null ? st.total_uploads : "-");
            setStat("ls-segments", st.segments != null ? st.segments : "-");
            setStat("ls-duration", st.last_duration != null ? st.last_duration + "s" : "-");

            const srcEl = document.getElementById("ls-source");
            if (srcEl) {
                srcEl.textContent = st.last_source || "-";
                srcEl.className = "ls-value" +
                    (st.last_source ? " src-" + st.last_source : "");
            }

            const last = data.last;

            if (last) {

                const badge = last.source === "phone" ? "phone" : "browser";

                liveLatest.innerHTML =
                    '<span class="live-filename">' + last.original_filename +
                    '<span class="live-badge ' + badge + '">' + badge.toUpperCase() +
                    '</span></span>' +
                    '<div class="live-grid">' +
                    '<span>Sample rate <b>' + last.sample_rate + ' Hz</b></span>' +
                    '<span>Duration <b>' + last.duration + ' s</b></span>' +
                    '<span>After trim <b>' + last.trimmed_duration + ' s</b></span>' +
                    '<span>Segments <b>' + last.segment_count + ' x ' +
                    last.segment_duration + 's</b></span>' +
                    '<span>Noise <b>' + last.noise_reduction_strength + '</b></span>' +
                    '<span>Received <b>' + ago(last.received_at) + '</b></span>' +
                    '</div>';

                /* pulse green only while something arrived recently */
                const fresh = (Date.now() / 1000 - last.received_at) < 120;
                liveDot.classList.toggle("stale", !fresh);

                if (last.received_at > lastSeenAt) {
                    lastSeenAt = last.received_at;

                    if (last.source === "phone") {

                        // SHOW PROCESSING SPINNER
                        if (uploadState) {
                            uploadState.style.display = "block";
                            uploadState.innerHTML = `
                                <div class="audio-loading-icon">
                                    ⟳
                                </div>

                                <h3>Processing Audio...</h3>

                                <p class="voice-upload-text">
                                    Converting and standardizing your audio.
                                    <br>
                                    Please wait...
                                </p>
                            `;
                        }

                        const body = new FormData();

                        body.append("filename", last.original_filename);
                        body.append("csrfmiddlewaretoken", csrf());

                        fetch("/process-existing/", {
                            method: "POST",
                            body: body
                        })
                        .then(function (r) {
                            return r.json();
                        })
                        .then(function (data) {
                            if (data.status === "success") {
                                showAudioResult(data);
                            }
                        });
                    }
                }

            }

            liveFiles.innerHTML = (data.files || []).map(function (f) {
                const fresh = (Date.now() / 1000 - f.modified) < 120 ? " fresh" : "";
                const safe = f.name.replace(/"/g, "&quot;");
                return '<li class="lf-row' + fresh + '" data-file="' + safe + '">' +
                       '<span class="lf-name">' + f.name + '</span>' +
                       '<span class="lf-meta">' + humanSize(f.size) + ' - ' +
                       ago(f.modified) + '</span>' +
                       '<span class="lf-go">PROCESS</span></li>';
            }).join("");

        } catch (err) {
            liveDot.classList.add("stale");
        }
    }

    refreshLive();
    setInterval(refreshLive, 3000);


    /* =========================================================
    CLICK A FILE IN THE LIVE FEED TO RE-PROCESS IT

    Delegated from the list, because the rows are rebuilt every poll
    and per-row listeners would be lost each refresh.
    ========================================================= */

    if (liveFiles) {

        liveFiles.addEventListener("click", function (ev) {

            const row = ev.target.closest("li.lf-row");
            if (!row) return;

            const filename = row.getAttribute("data-file");
            if (!filename || row.classList.contains("busy")) return;

            row.classList.add("busy");

            const token = (document.querySelector(
                "[name=csrfmiddlewaretoken]") || {}).value || "";

            const body = new FormData();
            body.append("filename", filename);
            body.append("csrfmiddlewaretoken", token);

            if (uploadState) uploadState.style.display = "none";
            if (errorBox) errorBox.style.display = "none";

            fetch("/process-existing/", { method: "POST", body: body })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    row.classList.remove("busy");
                    if (data.status === "success") {
                        showAudioResult(data);
                        refreshLive();
                    } else if (errorBox) {
                        errorBox.textContent = data.message || "Processing failed.";
                        errorBox.style.display = "block";
                    }
                })
                .catch(function (e) {
                    row.classList.remove("busy");
                    if (errorBox) {
                        errorBox.textContent = "Could not process: " + e.message;
                        errorBox.style.display = "block";
                    }
                });
        });
    }


    /* =========================================================
    SCAM MESSAGE DETECTION

    Textarea scores one pasted message; the list below shows whatever
    the Android app last pushed to /messages/.
    ========================================================= */

    const msgInput   = document.getElementById("msg-input");
    const msgAnalyze = document.getElementById("msg-analyze");
    const msgVerdict = document.getElementById("msg-verdict");
    const msgList    = document.getElementById("msg-list");
    const msgDevice  = document.getElementById("msg-device");
    const msgAnalyzeAll = document.getElementById("msg-analyze-all");

    function esc(t) {
        return String(t).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
                     '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    if (msgAnalyze && msgInput) {

        msgAnalyze.addEventListener("click", function () {

            const text = msgInput.value.trim();
            if (!text) return;

            const token = (document.querySelector(
                "[name=csrfmiddlewaretoken]") || {}).value || "";

            const useAi = msgAiBox && msgAiBox.checked && ai.enabled;

            const body = new FormData();
            body.append("text", text);
            body.append("ai", useAi ? "1" : "0");
            body.append("csrfmiddlewaretoken", token);

            msgAnalyze.disabled = true;
            msgAnalyze.textContent = useAi
                ? "Asking the LLM..."
                : "Analyzing...";

            fetch("/analyse-message/", { method: "POST", body: body })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    msgAnalyze.disabled = false;
                    msgAnalyze.textContent = "Analyze Content";

                    if (d.status !== "success") {
                        msgVerdict.style.display = "block";
                        msgVerdict.innerHTML =
                            '<p class="msg-clean">' + esc(d.message) + '</p>';
                        return;
                    }

                    msgVerdict.style.display = "block";
                    msgVerdict.innerHTML =
                        scoreBlock(d.score, d.level, d.language) +
                        adjustmentNote(d) +
                        (d.matched ? ruleList(d.matched) : reasonList(d.reasons)) +
                        linkPanel(d.links) +
                        aiPanel(d.ai, d.scam);
                })
                .catch(function (e) {
                    msgAnalyze.disabled = false;
                    msgAnalyze.textContent = "Analyze Content";
                    msgVerdict.style.display = "block";
                    msgVerdict.innerHTML =
                        '<p class="msg-clean">Could not analyse: ' +
                        esc(e.message) + '</p>';
                });
        });
    }

    let lastMessages = [];
    let lastDevice = null;
    let lastFlagged = 0;
    let analyzingAll = false;      // while true the button owns its own label

    /* The poll only refreshes the data; drawing is separate so a verdict
       arriving between two polls can repaint on its own. */
    function refreshMessages() {

        if (!msgList) return;

        fetch("/messages/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                lastMessages = data.messages || [];
                lastDevice = data.device;
                lastFlagged = data.flagged || 0;
                renderMessages();
            })
            .catch(function () { /* dashboard keeps working without it */ });
    }

    function renderMessages() {

        if (!msgList) return;

        if (msgDevice) {
            msgDevice.textContent = lastDevice
                ? lastDevice + " - " + lastMessages.length + " scanned, " +
                  lastFlagged + " flagged"
                : "no device yet";
        }

        if (msgAnalyzeAll && !analyzingAll) {

            const pending = lastMessages.filter(function (m) {
                return !aiByMessage[messageKey(m.sender, m.body)];
            }).length;

            msgAnalyzeAll.style.display =
                (ai.enabled && lastMessages.length) ? "inline-block" : "none";

            msgAnalyzeAll.textContent = pending
                ? "Analyze all (" + pending + ")"
                : "All analyzed";

            msgAnalyzeAll.disabled = !pending;
        }

        if (!lastMessages.length) {
            msgList.innerHTML =
                '<p class="msg-empty">Open the CallStream app and ' +
                'press SCAN MESSAGES.</p>';
            return;
        }

        msgList.innerHTML = lastMessages.map(function (m) {

            const why = m.reasons.length
                ? '<div class="msg-why"><b>Why:</b> ' +
                  esc(m.reasons.join(", ")) + "</div>"
                : '<div class="msg-why">No scam indicators matched.</div>';

            // the rule score is instant; the model is asked per message
            const key = messageKey(m.sender, m.body);
            const verdict = aiByMessage[key];

            let review = "";
            let clickable = "";

            if (verdict) {
                review = verdict.pending
                    ? '<div class="msg-pending">Scanning links and asking the LLM...</div>'
                    : linkPanel(verdict.links) +
                      aiPanel(verdict.ai, verdict.scam);
            } else if (ai.enabled) {
                clickable = " clickable";
                review = '<button type="button" class="msg-ai-btn">' +
                         "Analyze</button>";
            }

            return '<div class="msg-item ' + m.level + clickable + '"' +
                   ' data-key="' + esc(key) + '"' +
                   ' data-sender="' + esc(m.sender) + '"' +
                   ' data-body="' + esc(m.body) + '">' +
                   '<div class="msg-item-head">' +
                   '<span class="msg-sender">' + esc(m.sender) + '</span>' +
                   '<span>' + langChip(m.language) + 
                   '<span class="msg-level ' + m.level + '">' +
                   'Risk Factor: ' + m.level.toUpperCase() + " " + m.score + '%</span></span></div>' +
                   '<p class="msg-body">' + esc(m.body) + "</p>" +
                   why + review + "</div>";
        }).join("");
    }

    /* =========================================================
    LOCAL AI (OLLAMA) SETTINGS

    One config file server side; this tab reads and writes it and
    remembers whether the assistant is on, so the Message and URL
    tabs can grey out their "ask the model" boxes.
    ========================================================= */

    const setHost    = document.getElementById("set-host");
    const setModel   = document.getElementById("set-model");
    const setTimeout_ = document.getElementById("set-timeout");
    const setEnabled = document.getElementById("set-enabled");
    const setDot     = document.getElementById("set-dot");
    const setStatus  = document.getElementById("set-status-text");
    const setNote    = document.getElementById("set-note");
    const setTestOut = document.getElementById("set-test-out");

    const msgAiBox   = document.getElementById("msg-ai");
    const msgAiHint  = document.getElementById("msg-ai-hint");
    const urlAiBox   = document.getElementById("url-ai");
    const urlAiHint  = document.getElementById("url-ai-hint");

    const ai = { enabled: false };

    function csrf() {
        return (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
    }

    function postJSON(url, data) {
        return fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrf()
            },
            body: JSON.stringify(data)
        }).then(function (r) { return r.json(); });
    }

    function note(text, kind) {
        if (!setNote) return;
        setNote.style.display = "block";
        setNote.className = "set-note " + (kind || "");
        setNote.textContent = text;
    }

    /* Both "ask the model" checkboxes follow the saved setting. */
    function syncAiControls() {

        [[msgAiBox, msgAiHint], [urlAiBox, urlAiHint]].forEach(function (pair) {

            const box = pair[0], hint = pair[1];
            if (!box) return;

            box.disabled = !ai.enabled;
            box.checked = ai.enabled;

            if (hint) {
                hint.textContent = ai.enabled
                    ? "LLM ready"
                    : "off in LLM settings";
            }
        });
    }

    function applyLlmState(data) {

        const cfg = data.settings || {};

        ai.enabled = !!cfg.enabled;

        if (setHost && document.activeElement !== setHost) setHost.value = cfg.host || "";
        if (setTimeout_) setTimeout_.value = cfg.timeout || 60;
        if (setEnabled) setEnabled.checked = !!cfg.enabled;

        if (setModel) {

            const models = data.models || [];

            setModel.innerHTML = models.length
                ? models.map(function (m) {
                      // accuracy first: it is the reason to pick one
                      const mark = m.recommended ? "* " : "";
                      const acc = m.accuracy ? "  [" + m.accuracy + " " +
                                               m.grade + "]" : "";
                      const size = m.parameters ? "  (" + m.parameters + ")" : "";
                      return '<option value="' + esc(m.name) + '">' +
                             esc(mark + m.name + acc + size) + "</option>";
                  }).join("")
                : '<option value="">-- no models found --</option>';

            if (cfg.model) {
                if (!models.some(function (m) { return m.name === cfg.model; })) {
                    setModel.insertAdjacentHTML("afterbegin",
                        '<option value="' + esc(cfg.model) + '">' +
                        esc(cfg.model) + " (not installed)</option>");
                }
                setModel.value = cfg.model;
            }
        }

        if (setDot && setStatus) {

            if (data.connection_error) {
                setDot.className = "set-dot down";
                setStatus.textContent = data.connection_error;
            } else {
                const count = (data.models || []).length;
                setDot.className = "set-dot up";
                setStatus.textContent = "Ollama reachable - " + count +
                    " model" + (count === 1 ? "" : "s") + " installed" +
                    (cfg.enabled ? ", assistant ON" : ", assistant OFF");
            }
        }

        showRating(data.rating);
        syncAiControls();
    }

    /* What the current model actually scored on the accuracy suite. */
    function showRating(rating) {

        const box = document.getElementById("set-rating");
        if (!box) return;

        if (!rating) {
            box.style.display = "none";
            return;
        }

        const recommend = (rating.grade === "best" || rating.model === rating.best)
            ? ""
            : '<p class="set-help">Recommended: <b>' + esc(rating.best) +
              "</b> - 11/11 on the same suite.</p>";

        box.style.display = "block";
        box.className = "set-rating " + rating.grade;
        box.innerHTML =
            '<div class="set-rating-head"><span>Measured accuracy</span>' +
            '<span class="rating-grade ' + rating.grade + '">' +
            esc(rating.score) + " &middot; " + esc(rating.grade).toUpperCase() +
            "</span></div>" +
            "<p>" + esc(rating.note) + "</p>" + recommend;
    }

    function loadLlm(host) {

        if (!setHost) return;                 // settings tab not on the page

        const q = host ? "?host=" + encodeURIComponent(host) : "";

        fetch("/llm-settings/" + q, { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(applyLlmState)
            .catch(function (e) {
                if (setStatus) setStatus.textContent = "Could not read settings: " + e.message;
            });
    }

    const setRefresh = document.getElementById("set-refresh");

    if (setRefresh) {
        setRefresh.addEventListener("click", function () {
            if (setStatus) setStatus.textContent = "looking for models...";
            loadLlm(setHost.value.trim());
        });
    }

    const setSave = document.getElementById("set-save");

    if (setSave) {

        setSave.addEventListener("click", function () {

            setSave.disabled = true;
            setSave.textContent = "Saving...";

            postJSON("/llm-settings/", {
                host: setHost.value.trim(),
                model: setModel.value,
                timeout: setTimeout_.value,
                enabled: setEnabled.checked
            }).then(function (d) {

                setSave.disabled = false;
                setSave.textContent = "Save settings";

                if (d.status !== "success") {
                    note(d.message || "Could not save.", "bad");
                    return;
                }

                applyLlmState(d);
                note(d.settings.enabled
                    ? "Saved. The LLM will review messages and links."
                    : "Saved. The LLM is off; rules still run.", "good");

            }).catch(function (e) {
                setSave.disabled = false;
                setSave.textContent = "Save settings";
                note("Could not save: " + e.message, "bad");
            });
        });
    }

    const setTestBtn = document.getElementById("set-test");

    if (setTestBtn) {

        setTestBtn.addEventListener("click", function () {

            setTestBtn.disabled = true;
            setTestBtn.textContent = "Asking the LLM...";
            setTestOut.style.display = "block";
            setTestOut.innerHTML = '<p class="set-help">The first run also loads ' +
                'the LLM into memory, which can take a while.</p>';

            postJSON("/llm-test/", {
                host: setHost.value.trim(),
                model: setModel.value
            }).then(function (d) {

                setTestBtn.disabled = false;
                setTestBtn.textContent = "Test the LLM";

                if (d.status !== "success") {
                    setTestOut.innerHTML = '<p class="set-bad">' +
                        esc(d.message || "The model did not answer.") + "</p>";
                    return;
                }

                setTestOut.innerHTML =
                    '<p class="set-help">Sample: "' + esc(d.sample) + '"</p>' +
                    aiPanel(d.verdict) +
                    '<p class="set-help">answered in ' + d.seconds + "s</p>";

            }).catch(function (e) {
                setTestBtn.disabled = false;
                setTestBtn.textContent = "Test the LLM";
                setTestOut.innerHTML = '<p class="set-bad">' + esc(e.message) + "</p>";
            });
        });
    }

    loadLlm();


    /* =========================================================
    SHARED RENDERERS
    ========================================================= */

    /* Which language the message turned out to be. The markers that decided
       it go in the tooltip - same rule as everywhere else here: the answer is
       short, the reasoning is one hover away. */
    function langChip(language) {

        if (!language || language.code === "unknown") return "";

        const short = { en: "EN", hi: "HI", mr: "MR", hi_mr: "HI/MR" }[language.code]
                      || language.code.toUpperCase();

        const why = (language.markers || []).length
            ? language.name + " - matched " + language.markers.join(", ")
            : language.name + " - " + language.script + " script";

        return '<span class="lang-chip ' + language.code + '" title="' +
               esc(why) + '">' + 'Lang: '+ esc(short) + "</span>";
    }

    function scoreBlock(score, level, language) {
        return '<div class="msg-score"><span>Risk score ' + score +
               '%/100%</span><span>' + langChip(language) +
               '<span class="msg-level ' + level + '">' +
               String(level).toUpperCase() + '</span></span></div>' +
               '<div class="msg-bar"><span class="' + level +
               '" style="width:' + score + '%"></span></div>';
    }

    function reasonList(reasons) {
        if (!reasons || !reasons.length) {
            return '<p class="msg-clean">No suspicious indicators matched.</p>';
        }
        return '<ul class="msg-reasons">' + reasons.map(function (r) {
            return "<li>" + esc(r) + "</li>";
        }).join("") + "</ul>";
    }

    /* Every matched rule, including the ones the LLM struck out for reading
       the sentence the wrong way round. Struck through, never deleted - a
       score that silently changed is a score nobody can check. */
    function ruleList(matched) {

        if (!matched || !matched.length) {
            return '<p class="msg-clean">No suspicious indicators matched.</p>';
        }

        return '<ul class="msg-reasons">' + matched.map(function (r) {

            if (!r.overturned) {
                return "<li>" + esc(r.label) + " <b>+" + r.points + "</b></li>";
            }

            return '<li class="struck">' + esc(r.label) +
                   " <b>+" + r.points + "</b>" +
                   '<span class="struck-why">LLM: ' +
                   esc(r.why || "misread in context") + " &minus;" +
                   r.points + "</span></li>";
        }).join("") + "</ul>";
    }

    /* "30 -> 0 because the message warns against the thing the rule caught" */
    function adjustmentNote(d) {

        if (!d.adjusted) return "";

        return '<div class="score-adjusted">Rule score <b>' + d.rule_score +
               "</b> &rarr; <b>" + d.score + "</b> after the LLM read the " +
               "context. Struck rules below.</div>";
    }

    /* The yes/no answer. `scam` is the server's combined call - a link the
       scanner rated high says scam even when the model shrugged at it. */
    function aiPanel(verdict, scam) {

        if (!verdict) return "";

        if (scam === undefined) scam = verdict.scam;

        if (verdict.error) {
            return '<div class="ai-panel"><div class="ai-head">LLM</div>' +
                   '<p class="set-bad">' + esc(verdict.error) + "</p>" +
                   (verdict.raw ? '<pre class="ai-raw">' + esc(verdict.raw) + "</pre>" : "") +
                   "</div>";
        }

        const flags = (verdict.red_flags || []).length
            ? '<ul class="msg-reasons">' + verdict.red_flags.map(function (f) {
                  return "<li>" + esc(f) + "</li>";
              }).join("") + "</ul>"
            : "";

        // when the model is known to be weak on this message's language, the
        // verdict says so. A confident wrong answer with nothing beside it is
        // worse than no answer at all.
        const caveat = verdict.caveat
            ? '<p class="ai-caveat">' + esc(verdict.caveat) + "</p>"
            : "";

        return '<div class="ai-panel' + (verdict.caveat ? " uncertain" : "") + '">' +
               '<div class="ai-head">LLM' +
               (verdict.caveat
                   ? '<span class="ai-flag" title="' + esc(verdict.caveat) +
                     '">MAY BE INACCURATE</span>'
                   : "") +
               '<span class="ai-verdict ' + (scam ? "scam" : "clean") + '">' +
               (scam ? "SCAM" : "NOT SCAM") + "</span></div>" +
               (verdict.explanation ? "<p>" + esc(verdict.explanation) + "</p>" : "") +
               flags +
               caveat +
               (verdict.advice ? '<p class="ai-advice">' + esc(verdict.advice) + "</p>" : "") +
               "</div>";
    }

    /* What the scanner found at the end of each link in a message. */
    function linkPanel(links) {

        if (!links || !links.length) return "";

        return links.map(function (l) {

            const live = l.live || {};

            const where = live.final_url && live.final_url !== l.url
                ? " goes to " + esc(live.final_url)
                : "";

            const state = !live.checked ? "not visited"
                : live.blocked ? "internal address, not visited"
                : !live.resolves ? "domain does not exist"
                : live.tls === "invalid" ? "certificate INVALID"
                : !live.reachable ? "no answer"
                : "live, HTTP " + live.status +
                  (live.tls === "valid" ? ", certificate valid" : ", not encrypted") +
                  (live.title ? ', "' + esc(live.title) + '"' : "");

            return '<div class="msg-link ' + l.level + '">' +
                   '<div class="msg-link-head"><span class="msg-link-url">' +
                   esc(l.url) + "</span>" +
                   '<span class="msg-level ' + l.level + '">' +
                   l.level.toUpperCase() + " " + l.score + "</span></div>" +
                   '<div class="msg-link-state">' + state + where + "</div>" +
                   (l.reasons && l.reasons.length
                       ? '<div class="msg-why">' + esc(l.reasons.join(", ")) + "</div>"
                       : "") +
                   "</div>";
        }).join("");
    }


    /* =========================================================
    URL / LINK INSPECTION
    ========================================================= */

    const urlInput  = document.getElementById("url-input");
    const urlScan   = document.getElementById("url-scan");
    const urlLive   = document.getElementById("url-live");
    const urlResult = document.getElementById("url-result");

    function yesNo(value) {
        return value ? "yes" : "no";
    }

    function siteFacts(live) {

        if (!live || !live.checked) {
            return '<p class="set-help">The site itself was not visited - ' +
                   'only the address was read.</p>';
        }

        const rows = [];

        rows.push(["Domain resolves", live.resolves
            ? "yes" + (live.ip ? " (" + esc(live.ip) + ")" : "") : "no"]);

        rows.push(["Server answered", yesNo(live.reachable) +
            (live.status ? " - HTTP " + live.status : "")]);

        rows.push(["Transport", live.tls === "valid" ? "HTTPS, certificate valid"
            : live.tls === "invalid" ? "HTTPS, certificate INVALID"
            : live.tls === "none" ? "plain HTTP, not encrypted" : "-"]);

        if (live.final_url) rows.push(["Ends up at", esc(live.final_url)]);
        if (live.title) rows.push(["Page title", esc(live.title)]);
        if (live.server) rows.push(["Server header", esc(live.server)]);
        if (live.content_type) rows.push(["Content type", esc(live.content_type)]);
        if (live.reachable) rows.push(["Asks for a password", yesNo(live.has_password_field)]);
        if (live.is_download) rows.push(["Offers a download", "yes"]);

        let out = '<div class="url-facts">' + rows.map(function (r) {
            return '<div class="uf-cell"><span class="uf-label">' + r[0] +
                   '</span><span class="uf-value">' + r[1] + "</span></div>";
        }).join("") + "</div>";

        if (live.redirects && live.redirects.length) {
            out += '<div class="url-hops"><b>Redirect chain</b><ol>' +
                live.redirects.map(function (h) {
                    return "<li>" + h.status + " &rarr; " + esc(h.to) + "</li>";
                }).join("") + "</ol></div>";
        }

        if (live.error) {
            out += '<p class="set-bad">' + esc(live.error) + "</p>";
        }

        return out;
    }

    if (urlScan && urlInput) {

        function runUrlScan() {

            const target = urlInput.value.trim();
            if (!target) return;

            const body = new FormData();
            body.append("url", target);
            body.append("live", urlLive && urlLive.checked ? "1" : "0");
            body.append("ai", urlAiBox && urlAiBox.checked && ai.enabled ? "1" : "0");
            body.append("csrfmiddlewaretoken", csrf());

            urlScan.disabled = true;
            urlScan.textContent = "Scanning...";
            urlResult.style.display = "block";
            urlResult.innerHTML = '<p class="set-help">Reading the address' +
                (urlLive && urlLive.checked ? " and visiting the site" : "") +
                (urlAiBox && urlAiBox.checked && ai.enabled ? ", then asking the LLM" : "") +
                "...</p>";

            fetch("/analyse-url/", { method: "POST", body: body })
                .then(function (r) { return r.json(); })
                .then(function (d) {

                    urlScan.disabled = false;
                    urlScan.textContent = "Scan Link";

                    if (d.status !== "success") {
                        urlResult.innerHTML = '<p class="set-bad">' +
                            esc(d.message || "Scan failed.") + "</p>";
                        return;
                    }

                    urlResult.innerHTML =
                        '<div class="url-target">' + esc(d.url) +
                        '<span class="url-domain">' + esc(d.domain) + "</span></div>" +
                        scoreBlock(d.score, d.level) +
                        reasonList(d.reasons) +
                        siteFacts(d.live) +
                        aiPanel(d.ai);
                })
                .catch(function (e) {
                    urlScan.disabled = false;
                    urlScan.textContent = "Scan Link";
                    urlResult.innerHTML = '<p class="set-bad">Could not scan: ' +
                        esc(e.message) + "</p>";
                });
        }

        urlScan.addEventListener("click", runUrlScan);

        urlInput.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter") runUrlScan();
        });
    }


    /* =========================================================
    AI REVIEW OF A MESSAGE PUSHED BY THE PHONE

    Kept in a map because the list is rebuilt every poll - without
    this the verdict would vanish five seconds after it arrived.
    ========================================================= */

    const aiByMessage = {};

    function messageKey(sender, body) {
        return sender + " " + body;
    }

    /* One message through the model. Marks it pending first so the 5s poll
       cannot repaint the card back to "Ask ..." while the answer is in
       flight. Resolves either way - the caller loops over these. */
    function askModel(message) {

        const key = messageKey(message.sender, message.body);

        if (aiByMessage[key]) return Promise.resolve();   // done or in flight

        aiByMessage[key] = { pending: true };
        renderMessages();

        const body = new FormData();
        body.append("text", message.body);
        body.append("sender", message.sender);
        body.append("ai", "1");
        body.append("csrfmiddlewaretoken", csrf());

        return fetch("/analyse-message/", { method: "POST", body: body })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                aiByMessage[key] = {
                    ai: d.ai || { error: d.message || "No answer." },
                    links: d.links || [],
                    scam: !!d.scam,
                };
            })
            .catch(function (e) {
                aiByMessage[key] = { ai: { error: e.message }, links: [] };
            })
            .then(renderMessages);
    }

    if (msgList) {

        /* the whole card is the target - the button is only the affordance */
        msgList.addEventListener("click", function (ev) {

            const item = ev.target.closest(".msg-item");
            if (!item || !ai.enabled) return;

            const key = item.getAttribute("data-key");
            if (aiByMessage[key]) return;                 // already has an answer

            askModel({
                sender: item.getAttribute("data-sender") || "",
                body: item.getAttribute("data-body") || "",
            });
        });
    }

    if (msgAnalyzeAll) {

        msgAnalyzeAll.addEventListener("click", function () {

            // one at a time: Ollama serialises anyway, and a burst of ten
            // would just queue while eating memory
            const queue = lastMessages.filter(function (m) {
                return !aiByMessage[messageKey(m.sender, m.body)];
            });

            if (!queue.length) return;

            analyzingAll = true;
            msgAnalyzeAll.disabled = true;

            let done = 0;

            queue.reduce(function (chain, message) {
                return chain.then(function () {
                    msgAnalyzeAll.textContent =
                        "Analyzing " + (done + 1) + "/" + queue.length + "...";
                    return askModel(message).then(function () { done += 1; });
                });
            }, Promise.resolve()).then(function () {
                analyzingAll = false;
                renderMessages();
            });
        });
    }

    /* =========================================================
    LIVE 4-SECOND SEGMENTS

    The phone uploads a segment every 4 seconds while it is still
    recording. This polls for what has landed. Deliberately just
    shows them - analysis of live segments is a later module.
    ========================================================= */

    const segList    = document.getElementById("seg-list");
    const segSession = document.getElementById("seg-session");
    const segCount   = document.getElementById("seg-count");
    const segSeconds = document.getElementById("seg-seconds");
    const segAge     = document.getElementById("seg-age");

    let segShown = 0;

    function refreshSegments() {

        if (!segList) return;

        fetch("/live-segments/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (d) {

                const segments = d.segments || [];

                if (segSession) {
                    segSession.textContent = d.session
                        ? "session " + d.session
                        : "no recording yet";
                }

                if (segCount) segCount.textContent = segments.length;

                if (segSeconds) {
                    const total = segments.reduce(function (sum, s) {
                        return sum + (s.seconds || 0);
                    }, 0);
                    segSeconds.textContent = total.toFixed(1) + "s";
                }

                if (segAge && segments.length) {
                    const newest = segments[segments.length - 1].modified;
                    const age = Math.max(0, Math.round(Date.now() / 1000 - newest));
                    segAge.textContent = age < 10 ? "just now" : age + "s ago";
                    segAge.className = "ls-value" + (age < 10 ? " src-phone" : "");
                } else if (segAge) {
                    segAge.textContent = "-";
                }

                // only redraw when the count changed - otherwise the poll
                // restarts any <audio> element the user is listening to
                if (segments.length === segShown) return;
                segShown = segments.length;

                if (!segments.length) {
                    segList.innerHTML = "";
                    return;
                }

                // newest first: during a live call the latest segment is the
                // one worth hearing, and it should not require scrolling
                segList.innerHTML = segments.slice().reverse().map(function (s, i) {
                    return '<li class="seg-row' + (i === 0 ? " fresh" : "") + '">' +
                        '<span class="seg-index">#' +
                        (segments.length - i) + "</span>" +
                        '<audio controls preload="none" style="width:330px;flex:0 0 330px;" src="' + esc(s.url) +
                        '"></audio>' +
                        '<span class="seg-meta" style="margin-left:auto;text-align:right;">' +
                        s.seconds + "s &middot; " +
                        Math.round(s.bytes / 1024) + " KB</span></li>";
                }).join("");
            })
            .catch(function () { /* the rest of the dashboard is unaffected */ });
    }

    refreshSegments();
    setInterval(refreshSegments, 3000);


    /* started here, after the pieces refreshMessages() draws with exist */
    refreshMessages();
    setInterval(refreshMessages, 5000);

    /* =========================================================
    MODEL TRAINING
    Polls only while a run is active. Training takes half an hour,
    so a permanent 3s poll would be 600 pointless requests.
    ========================================================= */

    const trStart    = document.getElementById("tr-start");
    const trLog      = document.getElementById("tr-log");
    const trDot      = document.getElementById("tr-dot");
    const trText     = document.getElementById("tr-status-text");
    const trModel    = document.getElementById("tr-model");
    const trEer      = document.getElementById("tr-eer");
    const trThresh   = document.getElementById("tr-threshold");
    const trWhen     = document.getElementById("tr-when");
    const trSize     = document.getElementById("tr-size");
    const trElapsed  = document.getElementById("tr-elapsed");
    const trLimit    = document.getElementById("tr-limit");
    const trStop     = document.getElementById("tr-stop");
    const trProgress = document.getElementById("tr-progress");
    const trPhase    = document.getElementById("tr-phase");
    const trPercent  = document.getElementById("tr-percent");
    const trBarFill  = document.getElementById("tr-bar-fill");
    const trClips    = document.getElementById("tr-clips");
    const trElapsedL = document.getElementById("tr-elapsed-live");
    const trEta      = document.getElementById("tr-eta");
    const trRate     = document.getElementById("tr-rate");

    const trDatasetList = document.getElementById("tr-dataset-list");
    const trEpochs = document.getElementById("tr-epochs");
    let trDataset = "indicvoices_asvspoof5_mlaad";

    /* The picker only offers what is actually unpacked on disk. A dataset that
       is missing (or, for 2021 DF, present but unlabelled) is shown greyed with
       the reason, so the tab explains itself instead of failing at start. */
    function renderDatasets(list) {

        list = list || [];
        if (!trDatasetList) return;

        if (!list.length) {
            trDatasetList.innerHTML = '<p class="tr-empty">No datasets found.</p>';
            return;
        }

        // never leave the selection on something untrainable
        if (!list.some(function (d) { return d.key === trDataset && d.ready; })) {
            const first = list.filter(function (d) { return d.ready; })[0];
            trDataset = first ? first.key : "";
        }

        trDatasetList.innerHTML = list.map(function (d) {
            return '<label class="tr-dataset-row' +
                (d.key === trDataset ? " is-active" : "") +
                (d.ready ? "" : " is-off") + '">' +
                '<input type="radio" name="tr-dataset" value="' + esc(d.key) + '"' +
                (d.key === trDataset ? " checked" : "") +
                (d.ready ? "" : " disabled") + ">" +
                '<span class="tr-dataset-body">' +
                '<span class="tr-dataset-name">' + esc(d.name) + "</span>" +
                '<span class="tr-dataset-note">' + esc(d.note) + "</span>" +
                '<span class="tr-dataset-detail">' + esc(d.detail) + "</span>" +
                // only while something is still missing, so a ready dataset
                // does not nag about a download already done
                (d.show_url && d.url
                    ? '<a class="tr-dataset-link" href="' + esc(d.url) +
                      '" target="_blank" rel="noopener">' +
                      esc(d.url_label) + " &rarr;</a>"
                    : "") +
                "</span></label>";
        }).join("");

        trDatasetList.querySelectorAll("input[name=tr-dataset]").forEach(function (el) {
            el.addEventListener("change", function () {
                trDataset = el.value;
                renderDatasets(list);
            });
        });

        // the link lives inside the label, so a click would also hit the radio
        trDatasetList.querySelectorAll(".tr-dataset-link").forEach(function (a) {
            a.addEventListener("click", function (ev) { ev.stopPropagation(); });
        });

        if (trStart) trStart.disabled = !trDataset;
    }

    function refreshDatasets() {
        fetch("/train/datasets/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (d) { renderDatasets(d.datasets); })
            .catch(function () {
                if (trDatasetList) {
                    trDatasetList.innerHTML =
                        '<p class="tr-empty">Could not read the dataset list.</p>';
                }
            });
    }

    function trDuration(sec) {
        if (sec === null || sec === undefined) return "-";
        sec = Math.round(sec);
        if (sec < 60) return sec + "s";
        const m = Math.floor(sec / 60);
        if (m < 60) return m + "m " + (sec % 60) + "s";
        return Math.floor(m / 60) + "h " + (m % 60) + "m";
    }

    let trTimer = null;

    function trShowStatus(d) {

        const model = d.model || {};
        const running = d.state === "running";

        trDot.className = "tr-dot " +
            (running ? "busy" : (model.trained ? "up" : "down"));

        if (running) {
            trText.textContent = "training in progress - this takes a while";
        } else if (d.state === "stopped") {
            trText.textContent = "stopped - the previous model is untouched";
        } else if (d.state === "error") {
            trText.textContent = "training failed: " + (d.error || "unknown error");
        } else if (model.trained) {
            trText.textContent = "model trained - used for every upload";
        } else {
            trText.textContent = "no model yet - uploads use the heuristics only";
        }

        // --- live progress -------------------------------------------------
        trProgress.style.display = running ? "" : "none";
        trStop.style.display = running ? "" : "none";

        if (running) {
            trPhase.textContent = d.phase || "working";
            trPercent.textContent = (d.percent || 0).toFixed(1) + "%";
            trBarFill.style.width = (d.percent || 0) + "%";

            trClips.textContent = d.total_clips
                ? d.done_clips.toLocaleString() + " / " + d.total_clips.toLocaleString()
                : d.done_clips.toLocaleString();

            trElapsedL.textContent = trDuration(d.elapsed);
            trEta.textContent = d.eta === null ? "estimating" : trDuration(d.eta);
            trRate.textContent = d.elapsed > 0 && d.done_clips
                ? (d.done_clips / d.elapsed).toFixed(1)
                : "-";
        }

        if (model.trained) {
            trModel.style.display = "";
            trEer.textContent = model.fine_tuned ? "Fine-tuned" : "Forensics 0.3B";
            trThresh.textContent = model.fine_tuned ? "active.safetensors" : "base checkpoint";
            trWhen.textContent = new Date(model.saved_at * 1000)
                .toLocaleDateString(undefined, { month: "short", day: "numeric" });
            trSize.textContent = model.size_kb + " KB";
        }

        if (d.elapsed) {
            const m = Math.floor(d.elapsed / 60), sec = Math.round(d.elapsed % 60);
            trElapsed.textContent = (running ? "running " : "took ") +
                (m ? m + "m " : "") + sec + "s";
        }

        if (d.lines && d.lines.length) {
            // only scroll along if the user has not scrolled up to read
            const pinned = trLog.scrollTop + trLog.clientHeight >=
                           trLog.scrollHeight - 30;
            trLog.textContent = d.lines.join("\n");
            if (pinned) trLog.scrollTop = trLog.scrollHeight;
        }

        trStart.disabled = running;
        trLimit.disabled = running;
        if (trEpochs) trEpochs.disabled = running;
        trStop.disabled = false;

        if (!running && trTimer) {
            clearInterval(trTimer);
            trTimer = null;
            refreshModels();   // a finished run added one
            refreshDatasets(); // and may have prepared a corpus
        }
    }

    function trPoll() {
        fetch("/train/status/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(trShowStatus)
            .catch(function () { /* the rest of the dashboard is unaffected */ });
    }

    if (trStart) {

        trStart.addEventListener("click", function () {

            const body = new URLSearchParams();
            body.append("limit", trLimit.value || "");
            body.append("dataset", trDataset);
            body.append("epochs", trEpochs ? (trEpochs.value || "3") : "3");

            trStart.disabled = true;
            trLog.textContent = "starting...";

            fetch("/train/start/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-CSRFToken": csrf()
                },
                body: body
            })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (d.status !== "ok") {
                        trLog.textContent = d.message;
                        trStart.disabled = false;
                        return;
                    }
                    if (!trTimer) trTimer = setInterval(trPoll, 2000);
                    trPoll();
                })
                .catch(function (e) {
                    trLog.textContent = "could not start: " + e;
                    trStart.disabled = false;
                });
        });

        trStop.addEventListener("click", function () {
            trStop.disabled = true;
            fetch("/train/stop/", {
                method: "POST",
                headers: { "X-CSRFToken": csrf() }
            })
                .then(function (r) { return r.json(); })
                .then(trPoll)
                .catch(function () { trStop.disabled = false; });
        });

        // one read at load: shows an existing model, and resumes the live log
        // if a run is still going from before this page was opened
        fetch("/train/status/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                trShowStatus(d);
                if (d.state === "running" && !trTimer) {
                    trTimer = setInterval(trPoll, 2000);
                }
            })
            .catch(function () { /* the rest of the dashboard is unaffected */ });
    }

    /* =========================================================
    TRAINED MODELS
    One list, two views: rows with select/delete in the training
    tab, and a dropdown in Voice Detection. Both read /models/.
    ========================================================= */

    const trModelList = document.getElementById("tr-model-list");
    const vdPicker    = document.getElementById("vd-picker");
    const vdModel     = document.getElementById("vd-model");
    const vdUse       = document.getElementById("vd-use");
    const vdNote      = document.getElementById("vd-picker-note");

    function modelAge(ts) {
        const mins = (Date.now() / 1000 - ts) / 60;
        if (mins < 60) return Math.max(1, Math.round(mins)) + "m ago";
        if (mins < 1440) return Math.round(mins / 60) + "h ago";
        return Math.round(mins / 1440) + "d ago";
    }

    function renderModels(models) {

        models = models || [];

        if (trModelList) {
            trModelList.innerHTML = models.length
                ? models.map(function (m) {
                    return '<div class="tr-model-row' +
                        (m.active ? " is-active" : "") + '">' +
                        '<span class="tr-model-name">' + esc(m.name) +
                        // which dataset produced this model; blank on models
                        // trained before the name carried one
                        (m.dataset_name
                            ? '<span class="tr-model-dataset">' +
                              esc(m.dataset_name) + "</span>"
                            : '<span class="tr-model-dataset is-unknown">' +
                              "dataset unknown</span>") +
                        "</span>" +
                        '<span class="tr-model-meta">' +
                        (m.readable ? "EER " + m.eer + "% &middot; cutoff " + m.threshold
                                    : esc(m.detail || "unreadable")) +
                        (m.clips ? " &middot; " + m.clips.toLocaleString() + " clips" : "") +
                        " &middot; " + (m.size_kb > 999
                            ? (m.size_kb / 1024).toFixed(0) + " MB"
                            : m.size_kb + " KB") +
                        " &middot; " + modelAge(m.saved_at) + "</span>" +
                        (m.active
                            ? '<span class="tr-model-badge">active</span>'
                            : '<button class="tr-model-act" data-use="' + esc(m.name) +
                              '">Use</button>' +
                              // the pretrained model is a downloaded folder,
                              // not a file this app wrote, so it is not ours
                              // to delete
                              (m.pretrained
                                  ? ""
                                  : '<button class="tr-model-act" data-del="' +
                                    esc(m.name) + '">Delete</button>')) +
                        "</div>";
                }).join("")
                : '<p class="tr-empty">No models yet. Train one above.</p>';
        }

        // same list drives the "test one clip" picker in the training tab
        if (trTryModel) {
            const keep = trTryModel.value;
            trTryModel.innerHTML = models.map(function (m) {
                return '<option value="' + esc(m.name) + '">' + esc(m.name) +
                    (m.dataset_name ? "  [" + esc(m.dataset_name) + "]" : "") +
                    (m.readable ? "  (EER " + m.eer + "%)" : "  (unreadable)") +
                    "</option>";
            }).join("");
            if (keep) trTryModel.value = keep;      // keep the user's choice
            if (trTryPick) trTryPick.disabled = !models.length;
        }

        if (vdPicker && vdModel) {
            vdPicker.style.display = models.length ? "" : "none";
            vdModel.innerHTML = models.map(function (m) {
                return '<option value="' + esc(m.name) + '"' +
                    (m.active ? " selected" : "") + ">" + esc(m.name) +
                    (m.dataset_name ? "  [" + esc(m.dataset_name) + "]" : "") +
                    (m.readable ? "  (EER " + m.eer + "%)" : "  (unreadable)") +
                    (m.active ? "  - active" : "") + "</option>";
            }).join("");
        }
    }

    /* --- test one clip against a chosen model -------------------------- */

    const trTryModel  = document.getElementById("tr-try-model");
    const trTryFile   = document.getElementById("tr-try-file");
    const trTryPick   = document.getElementById("tr-try-pick");
    const trTryRun    = document.getElementById("tr-try-run");
    const trTryName   = document.getElementById("tr-try-name");
    const trTryResult = document.getElementById("tr-try-result");

    if (trTryPick && trTryFile) {
        trTryPick.addEventListener("click", function () { trTryFile.click(); });

        trTryFile.addEventListener("change", function () {
            const f = trTryFile.files[0];
            trTryName.textContent = f ? f.name : "";
            trTryRun.disabled = !f;
            trTryResult.hidden = true;
        });
    }

    if (trTryRun) {
        trTryRun.addEventListener("click", function () {
            const f = trTryFile.files[0];
            if (!f) return;

            const body = new FormData();
            body.append("audio", f);
            body.append("model", trTryModel.value);

            trTryRun.disabled = true;
            trTryResult.hidden = false;
            trTryResult.className = "tr-try-result";
            trTryResult.textContent = "scoring " + f.name + "...";

            fetch("/models/test/", {
                method: "POST",
                headers: { "X-CSRFToken": csrf() },
                body: body
            })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    trTryRun.disabled = false;
                    if (d.status !== "ok") {
                        trTryResult.className = "tr-try-result is-error";
                        trTryResult.textContent = d.message;
                        return;
                    }
                    const r = d.result;
                    const fake = r.verdict === "fake";
                    trTryResult.className =
                        "tr-try-result " + (fake ? "is-fake" : "is-real");
                    trTryResult.innerHTML =
                        '<span class="tr-try-verdict">' +
                        (fake ? "FAKE" : "REAL") + "</span>" +
                        '<span class="tr-try-detail">' +
                        "fake-probability " + r.fake_probability +
                        " &middot; cutoff " + r.threshold +
                        " &middot; " + esc(r.model_name) +
                        " (EER " + r.model_eer + "%)</span>";
                })
                .catch(function (e) {
                    trTryRun.disabled = false;
                    trTryResult.className = "tr-try-result is-error";
                    trTryResult.textContent = "could not score that file: " + e;
                });
        });
    }

    function refreshModels() {
        fetch("/models/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (d) { renderModels(d.models); })
            .catch(function () { /* the rest of the dashboard is unaffected */ });
    }

    function postModel(url, name, done) {
        const body = new URLSearchParams();
        body.append("name", name);
        fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": csrf()
            },
            body: body
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                renderModels(d.models);
                if (done) done(d);
            })
            .catch(function () { /* the rest of the dashboard is unaffected */ });
    }

    if (trModelList) {
        // delegated: the rows are re-rendered on every refresh
        trModelList.addEventListener("click", function (ev) {
            const use = ev.target.getAttribute("data-use");
            const del = ev.target.getAttribute("data-del");
            if (use) postModel("/models/select/", use, trPoll);
            if (del && window.confirm("Delete " + del + "? This cannot be undone."))
                postModel("/models/delete/", del, function (d) {
                    if (d.status !== "ok") window.alert(d.message);
                });
        });
    }

    if (vdUse && vdModel) {
        vdUse.addEventListener("click", function () {
            // Read the choice BEFORE the request: postModel re-renders the
            // list, which rebuilds these <option>s and resets vdModel.value.
            // Reading it in the callback scored whatever the select had
            // snapped back to, not what was clicked.
            const chosen = vdModel.value;
            postModel("/models/select/", chosen, function (d) {
                vdNote.textContent = d.message;
                // Switching the model used to leave the verdict on screen
                // untouched, so it still showed the previous model's score.
                // Re-score whatever is currently loaded with the new one.
                rescoreCurrentClip(chosen);
            });
        });
    }

    /* =========================================================
       FORENSIC REPORTS

       Every row is an analysis this system actually ran. The table
       used to be three hardcoded 2023 rows, which said nothing about
       the calls being tested.
       ========================================================= */

    const fxRows    = document.getElementById("forensic-rows");
    const fxCount   = document.getElementById("forensic-count");
    const fxRefresh = document.getElementById("forensic-refresh");
    const fxClear   = document.getElementById("forensic-clear");
    const fxModal   = document.getElementById("report-modal");
    const fxTitle   = document.getElementById("report-title");
    const fxBody    = document.getElementById("report-body");
    const fxClose   = document.getElementById("report-close");

    function fxStamp(ts) {
        const d = new Date(ts * 1000);
        const p = function (n) { return String(n).padStart(2, "0"); };
        return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
               " " + p(d.getHours()) + ":" + p(d.getMinutes());
    }

    function fxRiskClass(risk) {
        switch (risk) {
            case "CRITICAL": return "critical";
            case "HIGH":     return "critical";
            case "MEDIUM":   return "medium";
            case "LOW":      return "medium";
            default:         return "safe";
        }
    }

    function renderReports(reports) {

        if (!fxRows) return;
        reports = reports || [];

        if (fxCount) {
            fxCount.textContent = reports.length
                ? reports.length + (reports.length === 1 ? " report" : " reports")
                : "no analyses recorded yet";
        }

        if (!reports.length) {
            fxRows.innerHTML = '<tr><td colspan="5" class="forensic-empty">' +
                "Nothing analysed yet. Upload a clip, or send one from the " +
                "phone, and it appears here.</td></tr>";
            return;
        }

        // one glyph per evidence type, so voice / message / link rows are
        // distinguishable at a glance
        const glyph = { voice: "&#9835;", message: "&#9993;", url: "&#128279;" };

        fxRows.innerHTML = reports.map(function (r) {
            const fake = r.risk === "CRITICAL" || r.risk === "HIGH";
            return "<tr>" +
                '<td class="forensic-time">' + esc(fxStamp(r.at)) + "</td>" +
                "<td><div class=\"evidence-item\">" +
                '<span class="evidence-icon ' +
                (fake ? "voice-danger" : "voice-safe") + '">' +
                (glyph[r.kind] || "&#8594;") + "</span>" +
                '<span title="' + esc(r.label) + '">' + esc(r.label) + "</span>" +
                (r.source ? '<span class="evidence-src">' + esc(r.source) +
                            "</span>" : "") +
                "</div></td>" +
                '<td><span class="risk-badge ' + fxRiskClass(r.risk) + '">' +
                esc(r.risk || "?") + "</span></td>" +
                // title: the verdict clamps to two lines, so hover restores it
                '<td class="detection-result" title="' +
                esc(r.verdict || "") + '">' +
                '<span class="detection-text">' + esc(r.verdict || "-") +
                "</span>" +
                (r.model_name ? '<span class="detection-model">' +
                                esc(r.model_name) + "</span>" : "") +
                "</td>" +
                '<td><button class="view-report-btn" data-report="' + r.id +
                '">View Report</button></td>' +
                "</tr>";
        }).join("");
    }

    /* The filter row. Every control narrows the same server-side query, so
       the count always describes what is actually shown. */
    function reportQuery() {
        const p = new URLSearchParams();
        const grab = function (id, key) {
            const el = document.getElementById(id);
            if (el && el.value && el.value !== "all" && el.value !== "ALL") {
                p.append(key, el.value);
            }
        };
        grab("f-kind", "kind");
        grab("f-risk", "risk");
        grab("f-result", "result");
        grab("f-from", "from");
        grab("f-to", "to");
        grab("f-q", "q");
        const s = p.toString();
        return s ? "?" + s : "";
    }

    ["f-kind", "f-risk", "f-result", "f-from", "f-to"].forEach(function (id) {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", function () { refreshReports(); });
    });

    const fQ = document.getElementById("f-q");
    if (fQ) {
        // debounced: one request per pause, not one per keystroke
        let timer = null;
        fQ.addEventListener("input", function () {
            clearTimeout(timer);
            timer = setTimeout(refreshReports, 300);
        });
    }

    const fReset = document.getElementById("f-reset");
    if (fReset) {
        fReset.addEventListener("click", function () {
            ["f-kind", "f-risk", "f-result", "f-from", "f-to", "f-q"]
                .forEach(function (id) {
                    const el = document.getElementById(id);
                    if (!el) return;
                    if (el.tagName === "SELECT") el.selectedIndex = 0;
                    else el.value = "";
                });
            refreshReports();
        });
    }

    function refreshReports() {
        fetch("/forensic/reports/" + reportQuery(), { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (d) { renderReports(d.reports); })
            .catch(function () {
                if (fxRows) {
                    fxRows.innerHTML = '<tr><td colspan="5" class="forensic-empty">' +
                        "Could not load the history.</td></tr>";
                }
            });
    }

    function showReport(id) {
        fetch("/forensic/reports/" + id + "/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== "ok") return;
                const r = d.report;
                const f = (r.details && r.details.features) || {};

                if (fxTitle) fxTitle.textContent = r.label;

                let html =
                    '<div class="report-verdict ' + fxRiskClass(r.risk) + '">' +
                    esc(r.risk) + " &middot; " + esc(r.verdict) + "</div>" +
                    '<table class="report-table">' +
                    fxRow("Analysed", fxStamp(r.at)) +
                    fxRow("Evidence", r.label) +
                    fxRow("Source", r.source || "unknown") +
                    fxRow("Duration", r.duration ? r.duration + " s" : "-") +
                    fxRow("Fake probability",
                          r.score === null ? "-" : (r.score * 100).toFixed(1) + "%") +
                    fxRow("Model", r.model_name || "heuristics only") +
                    fxRow("Model EER",
                          r.model_eer === null ? "-" : r.model_eer + "%") +
                    fxRow("Cutoff", r.threshold === null ? "-" : r.threshold);

                // the measured acoustics, so the verdict can be checked rather
                // than taken on trust
                ["sample_rate", "rolloff", "centroid", "flatness",
                 "zero_crossing"].forEach(function (k) {
                    if (f[k] !== undefined) html += fxRow(k.replace(/_/g, " "), f[k]);
                });

                html += "</table>";

                if (r.media_url) {
                    html += '<audio class="report-audio" controls src="' +
                            esc(r.media_url) + '"></audio>';
                }

                if (fxBody) fxBody.innerHTML = html;

                // the PDF is generated server-side from the same row, so it
                // can never drift from what is on screen
                const dl = document.getElementById("report-pdf");
                if (dl) dl.href = "/forensic/reports/" + r.id + "/pdf/";
                const dx = document.getElementById("report-docx");
                if (dx) dx.href = "/forensic/reports/" + r.id + "/docx/";

                if (fxModal) fxModal.hidden = false;

                // Every detector explains itself: SHAP for the trained voice
                // models, rule attribution for messages and links. Computed on
                // demand, so the report opens first and this fills in.
                loadExplanation(r.id, r.kind);
            })
            .catch(function () { /* leave the table as it is */ });
    }

    function fxRow(k, v) {
        return "<tr><th>" + esc(k) + "</th><td>" + esc(String(v)) + "</td></tr>";
    }

    /* Explainable AI: which features moved the decision, when in the clip the
       evidence sits, and the spectrogram it was measured from. */
    function loadExplanation(id, kind) {

        const box = document.createElement("div");
        box.className = "xai-box";
        box.innerHTML = '<div class="xai-wait">computing explanation...</div>';
        if (fxBody) fxBody.appendChild(box);

        fetch("/forensic/reports/" + id + "/explain/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                const e = d.explanation || {};

                if (!e.available) {
                    // no SHAP is not the same as no explanation: show what was
                    // measured, and the spectrogram, when they exist
                    let h = '<h4 class="xai-title">Explanation</h4>' +
                            '<p class="xai-note">' +
                            esc(e.reason || "unavailable") + "</p>";
                    if (e.indicator_notes && e.indicator_notes.length) {
                        h += '<div class="xai-sub">Measured indicators</div><ul class="xai-list">';
                        e.indicator_notes.forEach(function (n) {
                            h += "<li>" + esc(n) + "</li>";
                        });
                        h += "</ul>";
                    }
                    if (e.measurements && Object.keys(e.measurements).length) {
                        h += '<div class="xai-sub">Measured acoustics</div>' +
                             '<div class="xai-facts">';
                        Object.keys(e.measurements).forEach(function (k) {
                            h += "<span><i>" + esc(k.replace(/_/g, " ")) +
                                 "</i>" + esc(String(e.measurements[k])) + "</span>";
                        });
                        h += "</div>";
                    }
                    if (e.spectrogram) h += spectroHtml(e.spectrogram);
                    box.innerHTML = h;
                    return;
                }

                let html = '<h4 class="xai-title">Why this verdict</h4>';

                const isVoice = (kind || e.kind) === "voice" || e.windows;
                const label = isVoice
                    ? (e.verdict === "fake" ? "AI-generated" : "real voice")
                    : (e.verdict === "flagged" ? "flagged" : "clean");

                html += '<div class="xai-conf">' +
                        (isVoice ? "Confidence <b>" + e.confidence + "%</b>"
                                 : "Rule score <b>" + e.confidence + "</b>") +
                        " &middot; " + label +
                        " &middot; " + esc(e.model) + "</div>";

                if (e.windows && e.windows.length) {
                    html += '<div class="xai-sub">Main contributing regions</div><ul class="xai-list">';
                    e.windows.forEach(function (w) {
                        html += "<li>" + w.start.toFixed(1) + " to " +
                                w.end.toFixed(1) + " s <span>(" +
                                (w.fake_probability * 100).toFixed(1) +          
                                // Math.round(w.fake_probability * 100) +
                                "% fake in this window)</span></li>";
                    });
                    html += "</ul>";
                }

                if (e.top_features && e.top_features.length) {
                    html += '<div class="xai-sub">' +
                        (isVoice ? "Features that moved the decision (SHAP)"
                                 : "Rules that produced this score") + "</div>";
                    const max = Math.max.apply(null, e.top_features.map(
                        function (f) { return Math.abs(f.contribution); })) || 1;
                    html += '<div class="xai-bars">';
                    e.top_features.forEach(function (f) {
                        const pct = Math.abs(f.contribution) / max * 100;
                        // voice: sign says which class. rules: every matched
                        // rule pushes the same way, so colour by the verdict.
                        const bad = isVoice ? f.contribution > 0
                                            : e.verdict === "flagged";
                        html += '<div class="xai-bar-row">' +
                            '<span class="xai-bar-name" title="' + esc(f.name) +
                            '">' + esc(f.name) + "</span>" +
                            '<span class="xai-bar-track"><span class="xai-bar-fill ' +
                            (bad ? "is-fake" : "is-real") + '" style="width:' +
                            pct.toFixed(0) + '%"></span></span>' +
                            '<span class="xai-bar-dir">' +
                            (isVoice ? (bad ? "AI" : "real")
                                     : (f.share ? f.share + "%" : "")) +
                            "</span></div>";
                    });
                    html += "</div>";
                }

                if (e.site && e.site.length) {
                    html += '<div class="xai-sub">What the link actually did</div>' +
                            '<ul class="xai-list">';
                    e.site.forEach(function (s) {
                        html += "<li>" + esc(s) + "</li>";
                    });
                    html += "</ul>";
                }

                if (e.narrative) {
                    html += '<p class="xai-narrative">' + esc(e.narrative) + "</p>";
                }

                if (e.spectrogram) html += spectroHtml(e.spectrogram);

                box.innerHTML = html;
            })
            .catch(function () {
                box.innerHTML = '<p class="xai-note">Explanation failed.</p>';
            });
    }

    function spectroHtml(b64) {
        return '<div class="xai-sub">Log-mel spectrogram</div>' +
               '<img class="xai-spectro" alt="Log-mel spectrogram" src="data:image/png;base64,' +
               b64 + '">';
    }

    /* ---- real-time alerts ------------------------------------------- */

    const alertBell  = document.getElementById("alert-bell");
    const alertCount = document.getElementById("alert-count");
    const alertPanel = document.getElementById("alert-panel");
    const alertList  = document.getElementById("alert-list");
    const alertSeen  = document.getElementById("alert-seen");

    let lastAlertId = 0;

    function refreshAlerts() {
        fetch("/alerts/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (alertCount) {
                    alertCount.textContent = d.unseen;
                    alertCount.hidden = !d.unseen;
                }
                if (alertBell) alertBell.classList.toggle("has-unseen", !!d.unseen);

                const rows = d.alerts || [];

                // a genuinely new alert pulses the bell once - the badge alone
                // is easy to miss on a dashboard someone is not watching
                if (rows.length && rows[0].id > lastAlertId) {
                    if (lastAlertId && alertBell) {
                        alertBell.classList.add("pulse");
                        setTimeout(function () {
                            alertBell.classList.remove("pulse");
                        }, 2000);
                    }
                    lastAlertId = rows[0].id;
                }

                if (!alertList) return;
                alertList.innerHTML = rows.length
                    ? rows.map(function (a) {
                        return '<div class="alert-item' +
                            (a.seen ? "" : " is-new") + '">' +
                            '<span class="alert-sev ' +
                            (a.severity === "CRITICAL" ? "critical" : "high") +
                            '">' + esc(a.severity) + "</span>" +
                            '<span class="alert-body"><b>' + esc(a.title) +
                            "</b><span>" + esc(a.detail) + "</span>" +
                            '<span class="alert-when">' + fxStamp(a.at) +
                            "</span></span></div>";
                    }).join("")
                    : '<p class="alert-empty">No alerts.</p>';
            })
            .catch(function () { /* the dashboard keeps working */ });
    }

    if (alertBell) {
        alertBell.addEventListener("click", function () {
            if (alertPanel) alertPanel.hidden = !alertPanel.hidden;
        });
    }
    if (alertSeen) {
        alertSeen.addEventListener("click", function () {
            fetch("/alerts/seen/", {
                method: "POST", headers: { "X-CSRFToken": csrf() }
            }).then(refreshAlerts).catch(function () {});
        });
    }
    document.addEventListener("click", function (ev) {
        if (alertPanel && !alertPanel.hidden &&
            !alertPanel.contains(ev.target) &&
            alertBell && !alertBell.contains(ev.target)) {
            alertPanel.hidden = true;
        }
    });

    /* ---- dashboard tiles, counted from the forensic log --------------- */

    function refreshStats() {
        fetch("/dashboard/stats/", { cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (s) {
                const put = function (id, v) {
                    const el = document.getElementById(id);
                    if (el) el.textContent = v;
                };
                put("stat-total", (s.total_scans || 0).toLocaleString());
                put("stat-voices", s.ai_voices || 0);
                put("stat-messages", s.scam_messages || 0);
                put("stat-links", s.malicious_links || 0);
                put("stat-highrisk", s.high_risk || 0);
                // put("stat-accuracy", s.detector_accuracy === null ||
                //     s.detector_accuracy === undefined
                //     ? "--" : s.detector_accuracy + "%");
                // const note = document.getElementById("stat-accuracy-note");
                // if (note) {
                //     note.textContent = s.detector
                //         ? "of " + s.detector + " (100 - EER)"
                //         : "no model selected";
                // }
                put("stat-accuracy", s.accuracy === null ||
                    s.accuracy === undefined
                    ? "--" : Number(s.accuracy).toFixed(2) + "%");

                const note = document.getElementById("stat-accuracy-note");
                if (note) {
                    note.textContent = s.accuracy !== null &&
                                       s.accuracy !== undefined
                        // ? "from validation metrics"
                        ? "of fine-tuned forensics-0.3B"
                        : "accuracy unavailable";
                }
            })
            .catch(function () { /* tiles keep their last value */ });
    }

    if (fxRows) {
        // delegated: rows are re-rendered on every refresh
        fxRows.addEventListener("click", function (ev) {
            const btn = ev.target.closest("[data-report]");
            if (btn) showReport(btn.getAttribute("data-report"));
        });
    }

    if (fxRefresh) fxRefresh.addEventListener("click", refreshReports);

    function closeReport() {
        if (fxModal) fxModal.hidden = true;
    }

    // three ways out: the x, the Close button, and Escape. A modal that can
    // only be dismissed one way is a modal someone gets stuck in.
    if (fxClose) fxClose.addEventListener("click", closeReport);

    const fxCancel = document.getElementById("report-cancel");
    if (fxCancel) fxCancel.addEventListener("click", closeReport);

    if (fxModal) {
        // click the backdrop, not the card, to dismiss
        fxModal.addEventListener("click", function (ev) {
            if (ev.target === fxModal) closeReport();
        });
    }

    document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape" && fxModal && !fxModal.hidden) closeReport();
    });

    if (fxClear) {
        fxClear.addEventListener("click", function () {
            if (!confirm("Delete the whole forensic history? This cannot be undone.")) return;
            fetch("/forensic/clear/", {
                method: "POST",
                headers: { "X-CSRFToken": csrf() }
            })
                .then(function (r) { return r.json(); })
                .then(function () { refreshReports(); })
                .catch(function () { /* ignore */ });
        });
    }

    refreshModels();
    refreshDatasets();
    refreshReports();
    refreshAlerts();
    refreshStats();

    // alerts are the only thing here that is time-critical; 10s is often
    // enough to notice without hammering the server
    setInterval(refreshAlerts, 10000);


});
