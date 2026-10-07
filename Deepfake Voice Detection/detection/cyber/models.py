from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """The project's user.

    Swapped in at the first migration on purpose. Django cannot change
    AUTH_USER_MODEL after tables exist without a rebuild, so the cheapest
    moment to own this model is before there is any data - which is now.

    Django's stock user allows duplicate email addresses; ours does not,
    because the sign-in form offers "email or phone" and a duplicate would
    make that ambiguous.
    """

    email = models.EmailField("email address", unique=True)

    phone = models.CharField(
        max_length=20,
        blank=True,
        help_text="Optional. Lets the analyst sign in with a phone number.",
    )

    def __str__(self):
        return self.get_username()


class ForensicReport(models.Model):
    """One analysed piece of evidence, kept so the Forensic tab has a real
    history instead of a hardcoded table.

    Written once, at the end of a successful analysis, and never updated. A
    forensic log that gets edited after the fact is not evidence, so rows are
    immutable by convention - re-analysing a file adds a new row rather than
    changing the old one.
    """

    VOICE = "voice"
    URL = "url"
    MESSAGE = "message"
    KIND_CHOICES = [
        (VOICE, "Voice clip"),
        (URL, "URL"),
        (MESSAGE, "Message"),
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=VOICE)

    # what was examined
    label = models.CharField(max_length=200, help_text="Filename, URL or sender")
    source = models.CharField(max_length=40, blank=True,
                              help_text="phone, browser, existing...")

    # the verdict. score is 0-1 and means "probability this is fake/malicious",
    # so one column works for every evidence type.
    score = models.FloatField(null=True, blank=True)
    verdict = models.CharField(max_length=40, blank=True)
    risk = models.CharField(max_length=10, blank=True)

    # how the verdict was reached - a report that cannot say which model
    # produced it is not much use in an investigation
    model_name = models.CharField(max_length=120, blank=True)
    model_eer = models.FloatField(null=True, blank=True)
    threshold = models.FloatField(null=True, blank=True)

    duration = models.FloatField(null=True, blank=True)
    media_url = models.CharField(max_length=400, blank=True)

    # the full analysis payload, so View Report can show the measurements
    # without re-running anything
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return "%s %s (%s)" % (self.kind, self.label, self.verdict or "?")

    @staticmethod
    def risk_for(score):
        """-> CRITICAL / HIGH / MEDIUM / LOW / SAFE.

        Bands, not a single cutoff: an analyst needs to see the difference
        between "just over the line" and "certain", and a bare probability
        does not communicate that at a glance."""
        if score is None:
            return "UNKNOWN"
        pct = score * 100
        if pct >= 90:
            return "CRITICAL"
        if pct >= 70:
            return "HIGH"
        if pct >= 50:
            return "MEDIUM"
        if pct >= 25:
            return "LOW"
        return "SAFE"


class TrustedVoice(models.Model):
    """A known voice to compare incoming calls against.

    Speaker verification, not deepfake detection - a different question. This
    answers "does this sound like the person it claims to be", which catches a
    caller impersonating a family member even when the audio is genuine.
    """

    name = models.CharField(max_length=80, help_text="Mother, Dad, Priya...")
    relation = models.CharField(max_length=60, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    audio_path = models.CharField(max_length=400)
    duration = models.FloatField(null=True, blank=True)

    # the voice print: a mean MFCC vector over the sample. Stored rather than
    # recomputed so a comparison does not have to re-read every enrolled clip.
    embedding = models.JSONField(default=list)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Alert(models.Model):
    """A raised alarm, kept so the dashboard can show what it has warned about.

    Written only when something crosses the alerting bar - a log of everything
    is the forensic table, and an alert stream that fires on every analysis is
    one nobody reads.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    report = models.ForeignKey(ForensicReport, on_delete=models.CASCADE,
                               null=True, blank=True, related_name="alerts")

    severity = models.CharField(max_length=10, default="HIGH")
    title = models.CharField(max_length=160)
    detail = models.TextField(blank=True)
    seen = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return "%s: %s" % (self.severity, self.title)
