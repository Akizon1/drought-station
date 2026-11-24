import hashlib
import hmac
import os

ROLES = ("admin", "user")


def make_hash(password, salt=None):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 200_000).hex()
    return salt, digest


def check_password(password, salt, stored_hash):
    _, digest = make_hash(password, salt)
    return hmac.compare_digest(digest, stored_hash)
