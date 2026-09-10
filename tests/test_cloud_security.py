from cloud_service.security import (
    TokenError,
    create_token,
    hash_password,
    read_token,
    reset_code_digest,
    verify_password,
)


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)


def test_signed_token_rejects_tampering_and_expiry():
    secret = "s" * 32
    token = create_token("user-1", "device-1", secret, ttl_seconds=60, now=100)
    assert read_token(token, secret, now=159)["sub"] == "user-1"

    try:
        read_token(token + "x", secret, now=159)
        assert False, "tampered token should fail"
    except TokenError:
        pass

    try:
        read_token(token, secret, now=160)
        assert False, "expired token should fail"
    except TokenError:
        pass


def test_reset_code_is_bound_to_account():
    secret = "s" * 32
    digest = reset_code_digest("user-1", "123456", secret)
    assert digest == reset_code_digest("user-1", "123456", secret)
    assert digest != reset_code_digest("user-2", "123456", secret)
