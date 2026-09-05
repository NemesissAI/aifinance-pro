"""Password policy, per NIST SP 800-63B.

The standard's finding, and the reason this file does not look like the rules
most sites use: composition rules ("one uppercase, one digit, one symbol") make
passwords *worse*. People satisfy them with Password1! and predictable
substitutions, so the rule shrinks the search space it was meant to widen.
What actually helps is length, and refusing passwords that are already on a
breach list — an attacker guesses from those lists, not from the alphabet.

So, deliberately:
  - a real length floor, and a high ceiling (long passphrases must be allowed)
  - every Unicode character accepted, spaces included
  - rejected if it is a known-breached or obvious password
  - rejected if it is mostly one repeated or sequential run
  - NO forced composition, NO forced expiry
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

MIN_LENGTH = 10          # above the NIST floor of 8; still passphrase-friendly
MAX_LENGTH = 128         # long enough for any passphrase, short enough to hash

# The passwords that actually show up at the top of every breach corpus, plus
# the ones this product invites by name. Small on purpose: the real defence is
# the online HIBP check below when the network is there.
COMMON = {
    "password", "passw0rd", "123456", "12345678", "123456789", "1234567890",
    "qwerty", "qwerty123", "111111", "123123", "abc123", "iloveyou",
    "admin", "letmein", "welcome", "monkey", "dragon", "sunshine", "princess",
    "football", "master", "hello", "freedom", "whatever", "trustno1",
    "aifinance", "finance", "money", "budget", "bankam", "sifre", "parola",
    "sifre123", "parola123", "asdasd", "asdf1234", "1q2w3e4r", "qazwsx",
    "galatasaray", "fenerbahce", "besiktas", "trabzonspor", "istanbul",
}

SEQUENCES = ("abcdefghijklmnopqrstuvwxyz", "0123456789",
             "qwertyuiop", "asdfghjkl", "zxcvbnm")


class WeakPassword(ValueError):
    """Carries a sentence the user can act on, not a rule number."""


def normalize(pw: str) -> str:
    """NFKC, so a password typed on another keyboard still matches."""
    return unicodedata.normalize("NFKC", pw)


def _is_sequential(pw: str) -> bool:
    low = pw.lower()
    if len(low) < 4:
        return False
    for seq in SEQUENCES:
        for i in range(len(seq) - 3):
            run = seq[i:i + 4]
            if run in low or run[::-1] in low:
                return True
    return False


def check(pw: str, *, email: str = "", name: str = "") -> None:
    """Raise WeakPassword with a usable reason, or return None."""
    pw = normalize(pw)

    if len(pw) < MIN_LENGTH:
        raise WeakPassword(
            f"Use at least {MIN_LENGTH} characters. A short phrase you will "
            "remember beats a short scramble you will not."
        )
    if len(pw) > MAX_LENGTH:
        raise WeakPassword(f"Keep it under {MAX_LENGTH} characters.")

    low = pw.lower()
    if low in COMMON or low.strip("0123456789!.") in COMMON:
        raise WeakPassword("That password appears on public breach lists. Pick another.")

    if len(set(pw)) <= 3:
        raise WeakPassword("Too few different characters — this is guessed quickly.")

    if _is_sequential(pw):
        raise WeakPassword("Avoid keyboard runs like 1234 or qwerty.")

    local = (email or "").split("@")[0].lower()
    for personal in (local, (name or "").lower()):
        if personal and len(personal) >= 4 and personal in low:
            raise WeakPassword("Do not put your name or email address in your password.")


async def is_breached(pw: str, *, timeout: float = 3.0) -> bool:
    """Have I Been Pwned, k-anonymity: only the first five characters of the
    SHA-1 ever leave this machine, never the password or the rest of the hash.
    Any network trouble returns False — a check that cannot run must not lock
    someone out of their own account.
    """
    try:
        import httpx
    except ImportError:
        return False
    digest = hashlib.sha1(normalize(pw).encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"https://api.pwnedpasswords.com/range/{prefix}",
                                 headers={"Add-Padding": "true"})
            if r.status_code != 200:
                return False
            for line in r.text.splitlines():
                h, _, count = line.partition(":")
                if h.strip() == suffix and count.strip("\r\n ") not in ("0", ""):
                    return True
    except Exception:
        return False
    return False


def strength_hint(pw: str) -> str:
    """A word for the meter. Length dominates, because it does in reality."""
    pw = normalize(pw)
    classes = sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w]"))
    if len(pw) >= 20 or (len(pw) >= 16 and classes >= 2):
        return "strong"
    if len(pw) >= MIN_LENGTH and (classes >= 2 or len(pw) >= 14):
        return "ok"
    return "weak"
