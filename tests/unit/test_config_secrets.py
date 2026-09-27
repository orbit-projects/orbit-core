# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
"""Tests for secret manager contracts and redaction guarantees."""

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from orbit.config import SecretReference, SecretValue


def test_secret_reference_rejects_embedded_credentials() -> None:
    assert SecretReference(uri="vault://secrets/api", version="7").uri == "vault://secrets/api"
    with pytest.raises(ValueError, match="credentials"):
        SecretReference(uri="vault://user:password@secrets/api")
    with pytest.raises(ValueError, match="host"):
        SecretReference(uri="vault://:443/secrets/api")
    with pytest.raises(ValueError, match="valid host and port"):
        SecretReference(uri="vault://secrets:invalid-port/api")
    with pytest.raises(ValueError, match="valid host and port"):
        SecretReference(uri="vault://[invalid-ipv6/secrets/api")
    with pytest.raises(ValueError, match="host"):
        SecretReference(uri="vault://secrets:")
    with pytest.raises(ValueError, match="host"):
        SecretReference(uri="vault://[fe80::1%25eth0]/secrets/api")
    with pytest.raises(ValueError, match="host"):
        SecretReference(uri="vault://secrets\\api")
    with pytest.raises(ValueError, match="printable"):
        SecretReference(uri="vault://secrets/api\x7f")
    with pytest.raises(ValueError, match="printable"):
        SecretReference(uri="vault://secrets/api", version="bad\nversion")
    with pytest.raises(ValidationError):
        SecretReference(uri=b"vault://secrets/api")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SecretReference(uri="vault://secrets/api", version=b"7")  # type: ignore[arg-type]


def test_secret_value_never_dumps_secret() -> None:
    secret = SecretValue(value=SecretStr("do-not-leak"), version="7", resolved_at=datetime.now(UTC))
    assert secret.reveal() == "do-not-leak"
    assert "do-not-leak" not in repr(secret)
    assert "do-not-leak" not in str(secret.model_dump())
    with pytest.raises(ValidationError):
        SecretValue(value=SecretStr("x"), version="")
    with pytest.raises(ValueError, match="printable"):
        SecretValue(value=SecretStr("x"), version="bad\nversion")
    with pytest.raises(ValidationError):
        SecretValue(value=SecretStr("x"), version=b"1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone"):
        SecretValue(value=SecretStr("x"), version="1", resolved_at=datetime(2026, 1, 1))
