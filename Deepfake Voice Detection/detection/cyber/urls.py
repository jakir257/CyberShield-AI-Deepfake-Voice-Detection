from django.contrib.auth import views as auth_views
from django.urls import path

from . import auth_views as cyber_auth
from . import views
from .views import upload_audio

urlpatterns = [
    path("", views.index, name="index"),

    # ---- analysis ------------------------------------------------------
    path("upload-audio/", upload_audio, name="upload_audio"),
    path("recent/", views.recent, name="recent"),
    path("process-existing/", views.process_existing, name="process_existing"),
    path("messages/", views.messages, name="messages"),
    path("analyse-message/", views.analyse_message, name="analyse_message"),
    path("analyse-url/", views.analyse_url, name="analyse_url"),
    path("llm-settings/", views.llm_settings, name="llm_settings"),
    path("llm-test/", views.llm_test, name="llm_test"),

    # live recording: the phone posts a 4s slice while still recording,
    # the dashboard polls for what has landed
    path("ping/", views.ping, name="ping"),
    path("upload-segment/", views.upload_segment, name="upload_segment"),
    path("live-segments/", views.live_segments, name="live_segments"),

    # ---- model training ------------------------------------------------
    # ---- forensic history --------------------------------------------
    path("forensic/reports/", views.forensic_reports, name="forensic_reports"),
    path("forensic/reports/<int:report_id>/", views.forensic_report,
         name="forensic_report"),
    path("forensic/reports/<int:report_id>/pdf/", views.forensic_pdf,
         name="forensic_pdf"),
    path("forensic/reports/<int:report_id>/explain/", views.forensic_explain,
         name="forensic_explain"),
    path("forensic/clear/", views.forensic_clear, name="forensic_clear"),
    path("forensic/reports/<int:report_id>/docx/", views.forensic_docx,
         name="forensic_docx"),
    path("dashboard/stats/", views.dashboard_stats, name="dashboard_stats"),

    # ---- alerts --------------------------------------------------------
    path("alerts/", views.alerts, name="alerts"),
    path("alerts/seen/", views.alerts_seen, name="alerts_seen"),

    # ---- trusted voices ------------------------------------------------
    path("voices/", views.trusted_voices, name="trusted_voices"),
    path("voices/add/", views.trusted_voice_add, name="trusted_voice_add"),
    path("voices/delete/", views.trusted_voice_delete,
         name="trusted_voice_delete"),
    path("voices/check/", views.trusted_voice_check, name="trusted_voice_check"),

    path("train/datasets/", views.train_datasets, name="train_datasets"),
    path("train/start/", views.train_start, name="train_start"),
    path("train/status/", views.train_status, name="train_status"),
    path("train/stop/", views.train_stop, name="train_stop"),
    path("models/", views.model_list, name="model_list"),
    path("models/select/", views.model_select, name="model_select"),
    path("models/delete/", views.model_delete, name="model_delete"),
    path("models/test/", views.model_test, name="model_test"),
    path("models/rescore/", views.model_rescore, name="model_rescore"),

    # ---- accounts ------------------------------------------------------
    path("signin/", cyber_auth.signin_page, name="signin_page"),
    path("auth/register/", cyber_auth.register, name="register"),
    path("auth/signin/", cyber_auth.signin, name="signin"),
    path("auth/signout/", cyber_auth.signout, name="signout"),
    path("auth/whoami/", cyber_auth.whoami, name="whoami"),

    # Password reset, all four of Django's own views. The email goes to the
    # console (settings.EMAIL_BACKEND), so the demo needs no SMTP server.
    path("password-reset/",
         auth_views.PasswordResetView.as_view(
             template_name="cyber/password_reset.html",
             email_template_name="cyber/password_reset_email.txt",
             success_url="/password-reset/sent/"),
         name="password_reset"),

    path("password-reset/sent/",
         auth_views.PasswordResetDoneView.as_view(
             template_name="cyber/password_reset_sent.html"),
         name="password_reset_done"),

    path("password-reset/<uidb64>/<token>/",
         auth_views.PasswordResetConfirmView.as_view(
             template_name="cyber/password_reset_confirm.html",
             success_url="/password-reset/done/"),
         name="password_reset_confirm"),

    path("password-reset/done/",
         auth_views.PasswordResetCompleteView.as_view(
             template_name="cyber/password_reset_done.html"),
         name="password_reset_complete"),
]
