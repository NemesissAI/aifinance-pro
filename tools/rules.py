"""What a transaction *means* — as data, not as code.

Every rule here used to be a hard-coded list inside the parser, which was fine
while this ran on one machine for one person and fatal the moment a second
person uploads a statement: "İsrafil Enes Saatçi means funds held in trust" is
true for exactly one user, and "MUSTAFAAÇIK is my father" is true for none of
the others.

So the rules split in two:

  DEFAULTS  — true for any Turkish bank statement. Card-payment wording, the
              shape of an own-account transfer. Ships to everybody.
  profile   — true for one person. Who they are, who pays them, who they hold
              money for, which broker they use. Supplied per user; empty is a
              perfectly valid profile and just means everything lands in the
              Unknown queue to be classified by hand.

A rule is (needle, note): `needle` is matched with `in` against the normalised
haystack, so it must already be in the plain-ASCII lowercase form norm() emits.
"""

from __future__ import annotations

# ── Defaults: generic, ship to every user ────────────────────────────────────

# A payment onto a credit card. Every Turkish bank words it differently and a
# missed spelling counts the payment as spending, so the list is long on
# purpose. Nothing here names a person.
DEFAULT_INTERNAL = [
    ("hesaptan odeme",         "Credit card payment from my own account"),
    ("odemeniz icin tesekkur", "Credit card payment from my own account"),
    ("kart odemesi",           "Credit card payment from my own account"),
    ("kk tahsilat",            "Credit card payment from my own account"),
    ("karti odeme",            "Credit card payment from my own account"),
    ("kredi karti borc",       "Credit card payment from my own account"),
]

# UPT is a money-transfer service, not a person: anyone using it is bridging
# funds, so the pair cancels out for them too.
DEFAULT_PASSTHROUGH = [
    ("uptodeme",  "UPT — bridge transfer"),
    ("upt odeme", "UPT — bridge transfer"),
]

# Brokers common enough in Turkey to be worth a default. A user who invests
# somewhere else adds it to their own profile.
DEFAULT_INVESTMENT = [
    ("midas",     "Transfer to a brokerage account"),
    ("gedik yat", "Transfer to a brokerage account"),
    ("is yatirim", "Transfer to a brokerage account"),
]

# Institutional senders that are income for whoever receives them.
DEFAULT_REGULAR_INCOME = [
    ("kredi yurtlar", "KYK study grant"),
    ("burs odemesi",  "Grant payment"),
    ("maas odemesi",  "Salary"),
    ("ucret odemesi", "Salary"),
]

# Nothing is a default here: money held for someone else is a claim only the
# account holder can make.
DEFAULT_TRUST: list[tuple[str, str]] = []


EMPTY_PROFILE: dict = {
    # Normalised name keys the account holder answers to, so a transfer to
    # themselves is recognised as internal rather than as a payment to a
    # stranger. person_key() produces these.
    "account_holder_keys": [],
    "regular_income": [],   # who reliably pays me, and what to call it
    "passthrough": [],      # people I bridge money through
    "investment": [],       # where I invest, beyond the defaults
    "trust": [],            # whose money I hold
}


def _pairs(raw) -> list[tuple[str, str]]:
    """Accept [[needle, note], ...] from JSON as well as tuples."""
    out = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((str(item[0]), str(item[1])))
        elif isinstance(item, dict) and "needle" in item:
            out.append((str(item["needle"]), str(item.get("note", ""))))
    return out


class RuleSet:
    """The defaults with one user's rules laid over them.

    The user's own entries come first, so a personal rule beats a generic one:
    somebody whose broker is also a friend's name should get their own answer.
    """

    def __init__(self, profile: dict | None = None):
        p = {**EMPTY_PROFILE, **(profile or {})}
        self.account_holder_keys = {str(k) for k in p["account_holder_keys"]}
        self.internal      = _pairs(p.get("internal")) + DEFAULT_INTERNAL
        self.passthrough   = _pairs(p["passthrough"])  + DEFAULT_PASSTHROUGH
        self.investment    = _pairs(p["investment"])   + DEFAULT_INVESTMENT
        self.trust         = _pairs(p["trust"])        + DEFAULT_TRUST
        self.regular_income = _pairs(p["regular_income"]) + DEFAULT_REGULAR_INCOME


def load(path) -> RuleSet:
    """Read a profile from disk; a missing file is an empty profile, not an error."""
    import json
    from pathlib import Path
    p = Path(path)
    if not p.is_file():
        return RuleSet()
    try:
        return RuleSet(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return RuleSet()
