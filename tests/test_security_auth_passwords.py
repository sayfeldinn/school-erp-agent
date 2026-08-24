from security.auth_passwords import hash_password, needs_rehash, verify_password


def test_password_hash_uses_argon2id():
    password = "temporary-password-for-hash-policy-test"

    encoded_hash = hash_password(password)

    assert isinstance(encoded_hash, str)
    assert password not in encoded_hash
    assert encoded_hash.startswith("$argon2id$")
    assert encoded_hash.split("$")[3] == "m=19456,t=2,p=1"


def test_correct_password_verifies():
    password = "temporary-correct-password"
    encoded_hash = hash_password(password)

    assert verify_password(password, encoded_hash) is True


def test_wrong_password_is_rejected_without_exception():
    encoded_hash = hash_password("temporary-correct-password")

    assert verify_password("temporary-wrong-password", encoded_hash) is False


def test_current_password_hash_does_not_need_rehash():
    encoded_hash = hash_password("temporary-password-for-rehash-test")

    assert needs_rehash(encoded_hash) is False
