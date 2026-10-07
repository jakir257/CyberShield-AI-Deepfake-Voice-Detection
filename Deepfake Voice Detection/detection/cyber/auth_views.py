"""
Register / sign in / sign out.

Thin on purpose. Django already rejects duplicate usernames and emails, weak
and common passwords, and bad credentials - re-implementing any of that would
be writing a worse copy of code that ships with the framework. These views
adapt its forms to the JSON the dashboard's existing sign-in card expects, and
nothing else.

Errors come back per field so the form can point at the offending input rather
than saying "something went wrong".
"""

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

User = get_user_model()


class RegisterForm(UserCreationForm):
    """UserCreationForm plus the fields our sign-up card actually collects.

    Password strength is not checked here - listing it as a field is what
    makes Django run AUTH_PASSWORD_VALIDATORS against it."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "phone")


def _errors(form):
    """Django's error dict -> {field: "first message"} for the UI."""

    return {field: errors[0] for field, errors in form.errors.items()}


@never_cache
def signin_page(request):
    """The sign-in card, served on its own URL so @login_required can send
    people here. Already signed in? Nothing to do here.

    @never_cache matters more than it looks. Without it the browser caches
    this page and the Back button restores it from memory without asking the
    server - so a signed-in user pressing Back lands on a login form that
    looks like they were logged out. The redirect below never ran, because
    nothing was ever requested."""

    if request.user.is_authenticated:
        return redirect("index")

    return render(request, "cyber/signin.html")


def register(request):

    if request.method != "POST":
        return JsonResponse({"status": "error",
                             "message": "Only POST requests are allowed."})

    form = RegisterForm(request.POST)

    if not form.is_valid():
        return JsonResponse({
            "status": "error",
            "message": "Please correct the highlighted fields.",
            "errors": _errors(form),
        })

    user = form.save()
    login(request, user)

    return JsonResponse({
        "status": "success",
        "username": user.get_username(),
        "display_name": user.get_full_name() or user.get_username(),
    })


def signin(request):

    if request.method != "POST":
        return JsonResponse({"status": "error",
                             "message": "Only POST requests are allowed."})

    identifier = request.POST.get("identifier", "").strip()
    password = request.POST.get("password", "")

    if not identifier or not password:
        return JsonResponse({
            "status": "error",
            "message": "Enter your username and password.",
        })

    # the form asks for "email or phone number", so accept any of the three
    # and hand authenticate() the username it actually wants
    username = identifier

    if "@" in identifier or identifier.replace("+", "").isdigit():

        match = User.objects.filter(
            Q(email__iexact=identifier) | Q(phone=identifier)
        ).first()

        if match:
            username = match.get_username()

    user = authenticate(request, username=username, password=password)

    if user is None:
        # deliberately does not say which half was wrong - that would confirm
        # whether an account exists to someone guessing
        return JsonResponse({
            "status": "error",
            "message": "Those details do not match an account.",
        })

    login(request, user)

    return JsonResponse({
        "status": "success",
        "username": user.get_username(),
        "display_name": user.get_full_name() or user.get_username(),
    })


@never_cache
def signout(request):
    """Ends the session. POST-only would be the textbook answer, but the
    confirmation dialog in the UI already stops an accidental click, and a
    link keeps the sidebar markup simple."""

    logout(request)

    response = redirect("signin_page")

    # after logout, Back must not resurrect the dashboard from cache
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"

    return response


def whoami(request):
    """Lets the page decide what to show without a round trip through a form."""

    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False})

    return JsonResponse({
        "authenticated": True,
        "username": request.user.get_username(),
        "display_name": (request.user.get_full_name()
                         or request.user.get_username()),
    })
