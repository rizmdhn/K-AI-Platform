"""Internal binding credential codec. Never register as an LLM/MCP tool."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
import json
import hmac
import os
import re
import stat
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_PASSWORD_BYTES = 4096


class CredentialError(ValueError):
    """Safe message only; contains no supplied secret or metadata."""


def _version(value):
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise CredentialError("Invalid credential key version.")


def _text(value, maximum):
    if not isinstance(value, str) or not value:
        raise CredentialError("Invalid credential data.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise CredentialError("Invalid credential data.") from None
    if len(encoded) > maximum:
        raise CredentialError("Invalid credential data.")
    return encoded


@dataclass(frozen=True)
class BindingContext:
    principal_id: UUID
    ch_username: str
    key_version: int
    binding_id: UUID
    service_target: str

    def __post_init__(self):
        if not isinstance(self.principal_id, UUID) or not isinstance(
            self.binding_id, UUID
        ):
            raise CredentialError("Invalid credential context.")
        _version(self.key_version)
        _text(self.ch_username, 1024)
        if (
            not isinstance(self.service_target, str)
            or len(self.service_target) > 512
            or not re.fullmatch(r"https://[a-z0-9.-]+:[0-9]{1,5}", self.service_target)
        ):
            raise CredentialError("Invalid credential service target.")


def _aad(context):
    if not isinstance(context, BindingContext):
        raise CredentialError("Invalid credential context.")
    fields = [
        "kai.ch-binding.v2",
        str(context.principal_id),
        context.ch_username,
        context.key_version,
        str(context.binding_id),
        context.service_target,
    ]
    return json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _key(keys, version):
    if not isinstance(keys, Mapping):
        raise CredentialError("Credential key is unavailable.")
    for number, key in keys.items():
        _version(number)
        if not isinstance(key, bytes) or len(key) != 32:
            raise CredentialError("Invalid credential key configuration.")
    if version not in keys:
        raise CredentialError("Credential key is unavailable.")
    return keys[version]


def encrypt_password(password, context, keys):
    plaintext = _text(password, MAX_PASSWORD_BYTES)
    aad = _aad(context)
    key = _key(keys, context.key_version)
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt_password(blob, context, keys):
    aad = _aad(context)
    key = _key(keys, context.key_version)
    if not isinstance(blob, bytes) or not 29 <= len(blob) <= 28 + MAX_PASSWORD_BYTES:
        raise CredentialError("Invalid encrypted credential.")
    try:
        password = AESGCM(key).decrypt(blob[:12], blob[12:], aad).decode("utf-8")
    except (InvalidTag, UnicodeError, ValueError):
        raise CredentialError("Invalid encrypted credential.") from None
    _text(password, MAX_PASSWORD_BYTES)
    return password


def rotate_password(blob, context, new_key_version, keys):
    _aad(context)
    _version(new_key_version)
    if new_key_version <= context.key_version:
        raise CredentialError("Rotation requires a newer key version.")
    updated = replace(context, key_version=new_key_version)
    if hmac.compare_digest(
        _key(keys, new_key_version), _key(keys, context.key_version)
    ):
        raise CredentialError("Rotation requires different key material.")
    password = decrypt_password(blob, context, keys)
    return updated, encrypt_password(password, updated, keys)


def _private(metadata, directory=False):
    valid_type = (
        stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    )
    allowed_modes = (0o500, 0o700) if directory else (0o400, 0o600)
    if (
        not valid_type
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) not in allowed_modes
    ):
        raise CredentialError("Credential key storage is not private.")


def load_keyring(directory):
    """Read a dedicated, runtime-owned private directory; never follow key symlinks."""
    try:
        descriptor = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            _private(os.fstat(descriptor), directory=True)
            keys = {}
            for name in os.listdir(descriptor):
                if not re.fullmatch(r"[1-9][0-9]*\.key", name):
                    raise CredentialError("Invalid credential key storage entry.")
                version = int(name[:-4])
                _version(version)
                # NONBLOCK avoids hanging on a FIFO; fstat then refuses every nonregular entry.
                key_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=descriptor,
                )
                try:
                    metadata = os.fstat(key_fd)
                    _private(metadata)
                    key = os.read(key_fd, 33)
                    if metadata.st_size != 32 or len(key) != 32:
                        raise CredentialError("Invalid credential key configuration.")
                    keys[version] = key
                finally:
                    os.close(key_fd)
            if not keys:
                raise CredentialError("Credential key is unavailable.")
            return keys
        finally:
            os.close(descriptor)
    except OSError:
        raise CredentialError("Credential key storage is unavailable.") from None
