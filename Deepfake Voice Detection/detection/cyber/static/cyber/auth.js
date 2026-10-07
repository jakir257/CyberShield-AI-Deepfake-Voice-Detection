/* Sign in / register.

   Replaces the old handler, which switched two divs and let anyone through
   whatever they typed. Every answer here comes from the server. */

document.addEventListener("DOMContentLoaded", function () {

    const signinForm = document.getElementById("signin-form");
    const signupForm = document.getElementById("signup-form");
    const authSignIn = document.getElementById("auth-signin");
    const authSignUp = document.getElementById("auth-signup");

    const gotoSignUp = document.getElementById("goto-signup-btn");
    const gotoSignIn = document.getElementById("goto-signin-btn");

    if (gotoSignUp) {
        gotoSignUp.addEventListener("click", function () {
            authSignIn.classList.add("hidden");
            authSignUp.classList.remove("hidden");
        });
    }

    if (gotoSignIn) {
        gotoSignIn.addEventListener("click", function () {
            authSignUp.classList.add("hidden");
            authSignIn.classList.remove("hidden");
        });
    }

    function csrf(form) {
        const field = form.querySelector("[name=csrfmiddlewaretoken]");
        return field ? field.value : "";
    }

    function showError(box, text) {
        if (!box) return;
        box.style.display = text ? "block" : "none";
        box.textContent = text || "";
    }

    /* One submit handler for both forms - they differ only in URL, in which
       fields they send, and in what a failure is called. */
    function wire(form, url, errorBox, collect) {

        if (!form) return;

        form.addEventListener("submit", function (ev) {

            ev.preventDefault();

            const button = form.querySelector("button[type=submit]");
            const original = button ? button.textContent : "";

            showError(errorBox, "");

            if (button) {
                button.disabled = true;
                button.textContent = "Working...";
            }

            const body = new FormData();
            body.append("csrfmiddlewaretoken", csrf(form));
            collect(body);

            fetch(url, { method: "POST", body: body })
                .then(function (r) { return r.json(); })
                .then(function (d) {

                    if (d.status === "success") {
                        // full navigation, not a div swap - the dashboard is
                        // a different page now and needs a session cookie
                        window.location.href = "/";
                        return;
                    }

                    if (button) {
                        button.disabled = false;
                        button.textContent = original;
                    }

                    // field errors are more use than a generic message
                    const fields = d.errors ? Object.keys(d.errors) : [];

                    showError(errorBox, fields.length
                        ? fields.map(function (f) { return d.errors[f]; }).join(" ")
                        : (d.message || "That did not work."));
                })
                .catch(function (e) {
                    if (button) {
                        button.disabled = false;
                        button.textContent = original;
                    }
                    showError(errorBox, "Could not reach the server: " + e.message);
                });
        });
    }

    wire(signinForm, "/auth/signin/",
         document.getElementById("signin-error"), function (body) {
             body.append("identifier",
                 (document.getElementById("signin-identifier") || {}).value || "");
             body.append("password",
                 (document.getElementById("signin-password") || {}).value || "");
         });

    wire(signupForm, "/auth/register/",
         document.getElementById("signup-error"), function (body) {
             const password =
                 (document.getElementById("signup-password") || {}).value || "";
             body.append("username",
                 (document.getElementById("signup-fullname") || {}).value || "");
             body.append("email",
                 (document.getElementById("signup-email") || {}).value || "");
             // UserCreationForm wants the password twice; the card only asks
             // once, so the confirmation is the same value
             body.append("password1", password);
             body.append("password2", password);
         });
});
