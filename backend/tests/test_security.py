from app.core.security import hash_password, verify_password


def test_password_hash_and_verify() -> None:
    password = "b3bc02e36552aea6"
    hashed = hash_password(password)
    assert hashed.startswith("$2")
    assert verify_password(password, hashed)
    assert not verify_password("wrong-password", hashed)
