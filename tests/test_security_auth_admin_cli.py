from security import auth_admin_cli
from security.auth_passwords import verify_password
from security.auth_store import SecurityStore


TENANT_ID = "school-a"
TENANT_NAME = "Al Noor School"
EMAIL = "teacher.ahmed@school-a.edu"
ROLE = "teacher"
VALID_PASSWORD = "correct horse battery staple"


def _seed_tenant(db_path):
    store = SecurityStore(db_path)
    try:
        store.initialize()
        store.create_tenant(tenant_id=TENANT_ID, name=TENANT_NAME)
    finally:
        store.close()


def _get_tenant(db_path):
    store = SecurityStore(db_path)
    try:
        store.initialize()
        return store.get_tenant(TENANT_ID)
    finally:
        store.close()


def _get_user(db_path):
    store = SecurityStore(db_path)
    try:
        store.initialize()
        return store.get_user_by_email(EMAIL)
    finally:
        store.close()


def _set_password_entries(monkeypatch, *entries):
    responses = iter(entries)
    prompts = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(auth_admin_cli.getpass, "getpass", fake_getpass)
    return prompts


def _create_user_args(db_path):
    return [
        "create-user",
        "--db",
        str(db_path),
        "--email",
        EMAIL,
        "--role",
        ROLE,
        "--tenant",
        TENANT_ID,
    ]


def test_create_tenant_creates_expected_tenant(tmp_path, capsys):
    db_path = tmp_path / "security.db"

    result = auth_admin_cli.main(
        [
            "create-tenant",
            "--db",
            str(db_path),
            "--id",
            TENANT_ID,
            "--name",
            TENANT_NAME,
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == "tenant created\n"
    assert captured.err == ""
    assert _get_tenant(db_path) == {
        "id": TENANT_ID,
        "name": TENANT_NAME,
        "status": "active",
        "created_at": _get_tenant(db_path)["created_at"],
    }


def test_create_user_creates_expected_user_under_existing_tenant(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "security.db"
    _seed_tenant(db_path)
    prompts = _set_password_entries(monkeypatch, VALID_PASSWORD, VALID_PASSWORD)

    result = auth_admin_cli.main(_create_user_args(db_path))

    captured = capsys.readouterr()
    user = _get_user(db_path)
    assert result == 0
    assert prompts == ["Password:", "Confirm password:"]
    assert captured.out == "user created\n"
    assert captured.err == ""
    assert user["email"] == EMAIL
    assert user["role"] == ROLE
    assert user["tenant_id"] == TENANT_ID
    assert user["status"] == "active"


def test_create_user_stores_argon2id_hash_not_plaintext(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "security.db"
    _seed_tenant(db_path)
    _set_password_entries(monkeypatch, VALID_PASSWORD, VALID_PASSWORD)

    assert auth_admin_cli.main(_create_user_args(db_path)) == 0

    encoded_hash = _get_user(db_path)["password_hash"]
    assert encoded_hash.startswith("$argon2id$")
    assert VALID_PASSWORD not in encoded_hash
    assert verify_password(VALID_PASSWORD, encoded_hash) is True


def test_password_confirmation_mismatch_creates_no_user(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "security.db"
    _seed_tenant(db_path)
    _set_password_entries(
        monkeypatch,
        VALID_PASSWORD,
        "different valid password phrase",
    )

    result = auth_admin_cli.main(_create_user_args(db_path))

    captured = capsys.readouterr()
    assert result != 0
    assert captured.out == ""
    assert captured.err == "password confirmation did not match\n"
    assert _get_user(db_path) is None


def test_password_shorter_than_fifteen_characters_is_rejected(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "security.db"
    _seed_tenant(db_path)
    short_password = "s" * 14
    _set_password_entries(monkeypatch, short_password, short_password)

    result = auth_admin_cli.main(_create_user_args(db_path))

    captured = capsys.readouterr()
    assert result != 0
    assert captured.out == ""
    assert captured.err == "password length is invalid\n"
    assert _get_user(db_path) is None


def test_password_longer_than_128_characters_is_rejected(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "security.db"
    _seed_tenant(db_path)
    long_password = "l" * 129
    _set_password_entries(monkeypatch, long_password, long_password)

    result = auth_admin_cli.main(_create_user_args(db_path))

    captured = capsys.readouterr()
    assert result != 0
    assert captured.out == ""
    assert captured.err == "password length is invalid\n"
    assert _get_user(db_path) is None


def test_create_user_does_not_accept_password_from_argv(
    tmp_path, monkeypatch, capsys
):
    password_value = "caller-supplied-password"
    prompted = False

    def unexpected_prompt(_prompt):
        nonlocal prompted
        prompted = True

    monkeypatch.setattr(auth_admin_cli.getpass, "getpass", unexpected_prompt)

    for index, password_args in enumerate(
        (["--password", password_value], [f"--password={password_value}"])
    ):
        db_path = tmp_path / f"security-{index}.db"
        result = auth_admin_cli.main([*_create_user_args(db_path), *password_args])
        captured = capsys.readouterr()

        assert result != 0
        assert prompted is False
        assert password_value not in captured.out
        assert password_value not in captured.err
        assert not db_path.exists()


def test_duplicate_operator_error_is_controlled_and_nonzero(tmp_path, capsys):
    db_path = tmp_path / "security.db"
    tenant_args = [
        "create-tenant",
        "--db",
        str(db_path),
        "--id",
        TENANT_ID,
        "--name",
        TENANT_NAME,
    ]
    assert auth_admin_cli.main(tenant_args) == 0
    capsys.readouterr()

    result = auth_admin_cli.main(tenant_args)

    captured = capsys.readouterr()
    tenant = _get_tenant(db_path)
    assert result != 0
    assert captured.out == ""
    assert captured.err == "tenant could not be created\n"
    assert "Traceback" not in captured.err
    assert "sqlite" not in captured.err.lower()
    assert "UNIQUE constraint" not in captured.err
    assert tenant["id"] == TENANT_ID
    assert tenant["name"] == TENANT_NAME
