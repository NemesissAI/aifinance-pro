"""Set an account's password from the machine the server runs on.

    .venv/Scripts/python.exe server/set_password.py someone@example.com

The new password is typed at a hidden prompt, never on the command line —
it would sit in the shell history and the process list otherwise.

This exists because there is no "forgot password" flow: the app sends no
email, so it cannot prove who is asking. The sign-up form masks the password
and asks for it once, which means one typo at registration locks the account
with no way back in from the browser. The owner of the machine can always
open the database, so this is the honest recovery path — same policy as
sign-up, so a recovered account is not a weaker one.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from argon2 import PasswordHasher  # noqa: E402

import db  # noqa: E402
import passwords  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0])
        print(f"usage: {argv[0]} EMAIL")
        return 2
    email = argv[1].strip().lower()

    db.init()
    with db.session() as s:
        u = db.user_by_email(s, email)
        if u is None:
            print(f"No account with the address {email!r}.")
            print("Accounts:", ", ".join(x.email for x in s.query(db.User).order_by(db.User.id)) or "none")
            return 1

        pw = getpass.getpass(f"New password for {email}: ")
        again = getpass.getpass("Type it again: ")
        if pw != again:
            print("The two entries differ — nothing changed.")
            return 1
        try:
            passwords.check(pw, email=u.email, name=u.name or "")
        except passwords.WeakPassword as e:
            print(f"Rejected: {e}")
            return 1

        u.password_hash = PasswordHasher().hash(passwords.normalize(pw))
        # Every cookie issued before this moment stops working, on every
        # device — the point of resetting a password is that whoever had the
        # old one (or a session from it) is out.
        u.session_version = (u.session_version or 0) + 1
        s.commit()
    print(f"Password set for {email}. Existing sessions were signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
