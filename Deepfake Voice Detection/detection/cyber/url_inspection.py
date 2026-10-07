"""
URL inspection: static heuristics + an actual visit to the site.

Same contract as scam_detection.py - a score out of 100, a level, and the list
of reasons that produced it, so the dashboard can show *why* rather than
asserting a number nothing computed.

Two halves:

  * read the URL     - shape, host, TLD, path. Free and instant.
  * visit the URL    - DNS, TLS, redirect chain, status, title, login form.
                       This is what answers "is this a real website".

The visit is deliberately conservative: http(s) only, public addresses only
(the dashboard must not become a port scanner for the LAN it runs on), a hard
read cap and a short timeout.
"""

import html
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

READ_LIMIT = 200_000        # enough for <head>, not enough to matter
DEFAULT_TIMEOUT = 8

SHORTENERS = {
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "is.gd", "buff.ly",
    "cutt.ly", "rb.gy", "shorturl.at", "rebrand.ly", "t.ly", "bl.ink",
    "shorte.st", "adf.ly", "tiny.cc", "clck.ru", "surl.li", "v.gd",
}

# TLDs that are cheap, disposable, or actively abused in phishing kits
RISKY_TLDS = {
    "zip", "mov", "top", "xyz", "tk", "ml", "ga", "cf", "gq", "work", "click",
    "link", "rest", "country", "kim", "loan", "men", "date", "racing", "win",
    "bid", "stream", "download", "review", "cam", "quest", "sbs", "cfd",
}

# brands whose name in a hostname is a lure unless it owns the domain
BRANDS = {
    "paypal": {"paypal.com"},
    "sbi": {"sbi.co.in", "onlinesbi.sbi", "onlinesbi.com", "sbi.com"},
    "hdfc": {"hdfcbank.com", "hdfc.com"},
    "icici": {"icicibank.com"},
    "axis": {"axisbank.com"},
    "paytm": {"paytm.com", "paytmbank.com"},
    "phonepe": {"phonepe.com"},
    "amazon": {"amazon.com", "amazon.in"},
    "flipkart": {"flipkart.com"},
    "netflix": {"netflix.com"},
    "microsoft": {"microsoft.com", "live.com", "office.com"},
    "apple": {"apple.com", "icloud.com"},
    "google": {"google.com", "google.co.in", "gmail.com", "youtube.com"},
    "facebook": {"facebook.com", "fb.com"},
    "instagram": {"instagram.com"},
    "whatsapp": {"whatsapp.com"},
    "aadhaar": {"uidai.gov.in"},
    "incometax": {"incometax.gov.in"},
}

LURE_WORDS = (
    "login", "signin", "sign-in", "verify", "verification", "secure", "update",
    "account", "kyc", "otp", "wallet", "netbanking", "refund", "prize",
    "reward", "unlock", "suspend", "confirm", "billing", "recover",
)

RISKY_FILES = (".apk", ".exe", ".scr", ".msi", ".bat", ".jar", ".vbs", ".dmg")

# two-part public suffixes we actually meet here
# ponytail: hand list, not the PSL. Swap in `tldextract` if this ever needs
# to be right for every ccTLD rather than the ones in scope.
MULTI_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.in", "net.in", "org.in",
    "gov.in", "ac.in", "edu.in", "res.in", "com.au", "com.br", "co.za",
    "co.jp", "com.sg", "com.my", "co.nz", "com.pk", "com.bd",
}

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
PASSWORD_RE = re.compile(r"<input[^>]+type\s*=\s*[\"']?password", re.I)
FORM_RE = re.compile(r"<form\b", re.I)
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def normalise(raw):
    """Add the scheme people leave off, strip whitespace. -> (url, parsed)."""

    url = (raw or "").strip().strip("<>\"'")

    if not url:
        return "", None

    if "://" not in url:
        url = "https://" + url

    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port                     # raises on "https://host:not-a-port"
    except ValueError:
        return url, None

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return url, None

    return url, parsed


def registrable(hostname):
    """example.co.uk from a.b.example.co.uk. Good enough for the TLDs in use."""

    host = (hostname or "").lower().strip(".")

    if not host or IPV4_RE.match(host):
        return host

    parts = host.split(".")

    if len(parts) < 2:
        return host

    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_SUFFIXES:
        return ".".join(parts[-3:])

    return ".".join(parts[-2:])


def _is_public(ip_text):
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False

    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _guard(url):
    """Raise unless this URL is safe for the server to fetch."""

    parsed = urllib.parse.urlsplit(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Refusing to fetch a %s:// URL." % parsed.scheme)

    host = parsed.hostname or ""

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise LookupError(str(e))

    ips = sorted({i[4][0] for i in infos})

    if not any(_is_public(ip) for ip in ips):
        raise ValueError(
            "%s points at a private or internal address - not scanned." % host)

    return ips


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Records the hops, and re-checks each one - a public host can redirect
    to 127.0.0.1 and urllib would follow it happily."""

    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({"status": code, "to": newurl[:400]})
        _guard(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _title(body):
    match = TITLE_RE.search(body)

    if not match:
        return ""

    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()[:200]


# ---------------------------------------------------------------------------
# the visit
# ---------------------------------------------------------------------------

def visit(url, timeout=DEFAULT_TIMEOUT):
    """Actually fetch the URL. Returns facts only - no scoring, no opinion."""

    facts = {
        "checked": True,
        "reachable": False,
        "resolves": False,
        "blocked": False,          # resolved fine, we refused to fetch it
        "ip": None,
        "tls": None,               # valid | invalid | none
        "status": None,
        "final_url": None,
        "final_host": None,
        "redirects": [],
        "title": "",
        "server": "",
        "content_type": "",
        "has_password_field": False,
        "has_form": False,
        "is_download": False,
        "error": None,
    }

    try:
        ips = _guard(url)
        facts["resolves"] = True
        facts["ip"] = ips[0]
        facts["ips"] = ips[:4]

    except LookupError as e:
        facts["error"] = "Domain does not resolve (%s)" % e
        return facts

    except ValueError as e:
        facts["resolves"] = True        # it resolved - we just refused to go
        facts["blocked"] = True
        facts["error"] = str(e)
        return facts

    redirects = _Redirects()
    opener = urllib.request.build_opener(
        redirects,
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )

    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })

    body = ""
    response = None

    try:
        response = opener.open(request, timeout=timeout)
        raw = response.read(READ_LIMIT)

    except urllib.error.HTTPError as e:
        # a 404 or 403 is still a live server - keep what it told us
        response = e
        raw = b""
        try:
            raw = e.read(READ_LIMIT)
        except Exception:
            pass

    except urllib.error.URLError as e:
        reason = e.reason

        if isinstance(reason, ssl.SSLCertVerificationError):
            facts["tls"] = "invalid"
            facts["error"] = "TLS certificate is not valid: %s" % reason.verify_message
        elif isinstance(reason, ssl.SSLError):
            facts["tls"] = "invalid"
            facts["error"] = "TLS handshake failed: %s" % reason
        else:
            facts["error"] = "Could not connect: %s" % reason

        facts["redirects"] = redirects.chain
        return facts

    except ValueError as e:
        # raised by our own guard from inside a redirect
        facts["error"] = str(e)
        facts["redirects"] = redirects.chain
        return facts

    except Exception as e:
        facts["error"] = "Fetch failed: %s" % e
        facts["redirects"] = redirects.chain
        return facts

    facts["reachable"] = True
    facts["redirects"] = redirects.chain
    facts["status"] = getattr(response, "status", None) or response.getcode()
    facts["final_url"] = response.geturl()[:400]
    facts["final_host"] = urllib.parse.urlsplit(facts["final_url"]).hostname
    facts["tls"] = "valid" if facts["final_url"].startswith("https") else "none"

    headers = response.headers
    facts["server"] = str(headers.get("Server", ""))[:80]
    facts["content_type"] = str(headers.get("Content-Type", ""))[:80]

    disposition = str(headers.get("Content-Disposition", ""))
    facts["is_download"] = "attachment" in disposition.lower()

    if "html" in facts["content_type"].lower() or not facts["content_type"]:
        body = raw.decode("utf-8", "replace")
        facts["title"] = _title(body)
        facts["has_password_field"] = bool(PASSWORD_RE.search(body))
        facts["has_form"] = bool(FORM_RE.search(body))

    try:
        response.close()
    except Exception:
        pass

    return facts


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def inspect(raw_url, live=True, timeout=DEFAULT_TIMEOUT):
    """-> {url, score, level, reasons[], host, domain, live{...}}."""

    url, parsed = normalise(raw_url)

    if not url:
        return {"error": "Nothing to check."}

    if parsed is None:
        return {"error": "That is not an http(s) URL."}

    host = (parsed.hostname or "").lower()
    domain = registrable(host)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    reasons = []
    score = 0

    def flag(points, why):
        nonlocal score
        score += points
        reasons.append(why)

    # ---- shape of the URL -------------------------------------------------

    if parsed.scheme == "http":
        flag(15, "plain http - the connection is not encrypted")

    if "@" in (parsed.netloc or ""):
        flag(30, "an @ in the address hides the host you actually reach")

    if IPV4_RE.match(host):
        flag(25, "a raw IP address instead of a domain name")

    if "xn--" in host:
        flag(25, "punycode host - can spell a brand with lookalike letters")

    if domain in SHORTENERS:
        flag(25, "shortened link - the real destination is hidden")

    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""

    if tld in RISKY_TLDS:
        flag(15, "the .%s domain space is heavily abused for phishing" % tld)

    labels = [p for p in host.split(".") if p]

    if len(labels) > 4:
        flag(10, "unusually deep subdomain chain (%d levels)" % len(labels))

    if len(host) > 40:
        flag(5, "very long hostname")

    if parsed.port and parsed.port not in (80, 443):
        flag(10, "served on non standard port %s" % parsed.port)

    name = domain.split(".")[0] if domain else ""

    if name.count("-") >= 2 or sum(c.isdigit() for c in name) >= 4:
        flag(10, "domain name padded with hyphens or digits")

    for brand, owned in BRANDS.items():
        if brand in host and domain not in owned:
            flag(30, "uses the name '%s' but the domain is %s, not theirs"
                 % (brand, domain or "unknown"))
            break

    if any(w in path or w in host.split(".", 1)[0] for w in LURE_WORDS):
        flag(15, "path or subdomain pushes a login / verify / claim action")

    if any(path.endswith(ext) for ext in RISKY_FILES):
        flag(25, "links straight to an installable file")

    if "http" in query or "%3a%2f%2f" in query:
        flag(10, "another URL is buried in the query string")

    # ---- what the site actually does --------------------------------------

    facts = {"checked": False}

    if live:
        facts = visit(url, timeout=timeout)

        if facts["blocked"]:
            reasons.append("private or internal address - the scanner will "
                           "not fetch it")

        elif not facts["resolves"]:
            flag(20, "the domain does not resolve - no such site")

        elif not facts["reachable"]:
            if facts["tls"] == "invalid":
                flag(30, "TLS certificate does not validate")
            else:
                flag(10, "the site did not respond")

        else:
            final_domain = registrable(facts["final_host"] or "")

            if facts["redirects"]:
                if final_domain and final_domain != domain:
                    flag(15, "redirects to a different site: %s" % final_domain)
                else:
                    reasons.append("redirects %d time(s) within %s"
                                   % (len(facts["redirects"]), domain))

            if facts["is_download"] or any(
                    (facts["final_url"] or "").lower().endswith(ext)
                    for ext in RISKY_FILES):
                flag(25, "the page hands you a file download")

            if facts["has_password_field"]:
                flag(10, "the page asks for a password")

            if facts["status"] and facts["status"] >= 400:
                flag(5, "the server answered HTTP %s" % facts["status"])

            if facts["tls"] == "none":
                flag(10, "final page is served over plain http")

    score = min(score, 100)

    if score >= 60:
        level = "high"
    elif score >= 30:
        level = "medium"
    else:
        level = "low"

    return {
        "url": url,
        "host": host,
        "domain": domain,
        "score": score,
        "level": level,
        "reasons": reasons,
        "live": facts,
    }


def _selftest():
    """Offline only - live=False, so nothing here touches the network."""

    assert registrable("a.b.example.co.uk") == "example.co.uk"
    assert registrable("www.google.com") == "google.com"
    assert registrable("localhost") == "localhost"
    assert registrable("8.8.8.8") == "8.8.8.8"

    clean = inspect("https://www.wikipedia.org/wiki/Fraud", live=False)
    assert clean["level"] == "low", clean
    assert clean["score"] == 0, clean
    assert clean["domain"] == "wikipedia.org", clean

    short = inspect("http://bit.ly/3xKq", live=False)
    assert short["level"] in ("medium", "high"), short

    fake = inspect("http://sbi-secure-verify.xyz/login/kyc.php", live=False)
    assert fake["level"] == "high", fake
    assert any("sbi" in r for r in fake["reasons"]), fake["reasons"]

    apk = inspect("http://192.168.99.5/update.apk", live=False)
    assert apk["level"] == "high", apk

    at_trick = inspect("https://paypal.com@evil.example.com/", live=False)
    assert at_trick["level"] in ("medium", "high"), at_trick

    # a real bank login page must not be called high on shape alone
    real = inspect("https://netbanking.hdfcbank.com/netbanking/", live=False)
    assert real["level"] in ("low", "medium"), real

    assert inspect("")["error"]
    assert inspect("javascript:alert(1)")["error"]
    assert inspect("ftp://files.example.com/x")["error"]

    assert not _is_public("127.0.0.1")
    assert not _is_public("192.168.1.10")
    assert not _is_public("::1")
    assert _is_public("8.8.8.8")

    assert _title("<html><head><title> Hello &amp; bye </title>") == "Hello & bye"

    print("url_inspection selftest ok")


if __name__ == "__main__":
    _selftest()
