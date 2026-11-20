"""Canonical identities shared by independent discovery, SQLite and degree-specific joins."""
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit, urlunsplit


def url_key(url):
    """Compare exact pages, tolerating fragments, host case and a trailing slash."""
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {"https", "http"} or not parts.hostname or parts.username
                or parts.password or any(c.isspace() or c in '<>\\' for c in url)):
            return ""
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))
    except ValueError:
        return ""


def norm(value):
    return " ".join(value.casefold().split())


def program_key(c):
    if not c.get("program_url") or not c.get("degree"):
        return None
    qualification = norm(c.get("qualification","")).replace(".", "")
    awards = {"master of science":"msc", "master of science (msc)":"msc", "doctor of philosophy":"phd",
              "doctor of philosophy (phd)":"phd", "master of arts":"ma", "master of arts (ma)":"ma"}
    subject = norm(c["subject"]).replace(".", "")
    subject = re.sub(r"\b(master of science|master of arts|doctor of philosophy|msc|phd|ma)\b", " ", subject)
    subject = re.sub(r"[()\[\],]", " ", subject)
    subject = re.sub(r"^\s*(in|of)\s+", "", subject).strip()
    if not subject:
        return None
    return [url_key(c["program_url"]), c["degree"], awards.get(qualification, qualification), norm(subject)]


def entity_key(c):
    identity = [program_key(c), url_key(c.get("profile_url", "")), norm(c.get("person","")), norm(c.get("campus", "")),
                norm(c.get("track", "")), c.get("pathway", ""), c.get("cycle", "")]
    if not any(identity[:2]):
        identity += [norm(c["institution"]), norm(c["subject"])]
    return sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:24]
