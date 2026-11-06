import dataclasses
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid

from credential_crypto import (
    BindingContext, CredentialError, decrypt_password, encrypt_password, load_keyring, rotate_password,
)


class CredentialCryptoTests(unittest.TestCase):
    def test_service_target_is_authenticated_without_legacy_downgrade(self):
        context=BindingContext(uuid.uuid4(),'tester',1,uuid.uuid4(),'https://staging.example:8443')
        keys={1:b'k'*32}
        blob=encrypt_password('private',context,keys)
        self.assertEqual(decrypt_password(blob,context,keys),'private')
        for target in ('https://production.example:8443',):
            with self.assertRaises(CredentialError):
                decrypt_password(blob,dataclasses.replace(context,service_target=target),keys)

    def setUp(self):
        self.context = BindingContext(uuid.uuid4(), 'personal_alice', 1, uuid.uuid4(), 'https://staging.example:8443')
        self.keys = {1: os.urandom(32), 2: os.urandom(32)}

    def test_roundtrip_preserves_whitespace_unicode_and_password_bytes(self):
        for password in ('password', '  secret\t\n', 'rahasia🔐é', 'a' * 4096, 'é' * 2048):
            with self.subTest(length=len(password)):
                blob = encrypt_password(password, self.context, self.keys)
                self.assertEqual(decrypt_password(blob, self.context, self.keys), password)
                self.assertEqual(len(blob), 28 + len(password.encode()))
                self.assertNotIn(password.encode(), blob)

    def test_same_password_gets_distinct_random_nonces(self):
        blobs = [encrypt_password('secret', self.context, self.keys) for _ in range(32)]
        self.assertEqual(len({blob[:12] for blob in blobs}), 32)

    def test_all_authenticated_context_fields_are_required(self):
        blob = encrypt_password('secret', self.context, self.keys)
        for changes in ({'principal_id': uuid.uuid4()}, {'ch_username': 'personal_bob'},
                        {'key_version': 2}, {'binding_id': uuid.uuid4()}):
            with self.subTest(changes=list(changes)):
                with self.assertRaises(CredentialError):
                    decrypt_password(blob, dataclasses.replace(self.context, **changes), self.keys)

    def test_key_nonce_ciphertext_and_tag_tampering_fail_without_secret_errors(self):
        password = 'do-not-log-this-password'
        blob = encrypt_password(password, self.context, self.keys)
        for position in (0, 11, 12, len(blob) - 17, len(blob) - 1):
            altered = bytearray(blob)
            altered[position] ^= 1
            with self.assertRaises(CredentialError) as caught:
                decrypt_password(bytes(altered), self.context, self.keys)
            self.assertNotIn(password, str(caught.exception))
            self.assertNotIn(blob.hex(), str(caught.exception))
        with self.assertRaises(CredentialError):
            decrypt_password(blob, self.context, {1: os.urandom(32)})

    def test_rotation_rewraps_without_changing_identity_or_ch_password(self):
        blob = encrypt_password('secret', self.context, self.keys)
        context, rotated = rotate_password(blob, self.context, 2, self.keys)
        self.assertEqual(context, dataclasses.replace(self.context, key_version=2))
        self.assertNotEqual(blob[:12], rotated[:12])
        self.assertEqual(decrypt_password(rotated, context, {2: self.keys[2]}), 'secret')
        with self.assertRaises(CredentialError):
            decrypt_password(blob, self.context, {2: self.keys[2]})
        with self.assertRaises(CredentialError):
            decrypt_password(rotated, self.context, self.keys)
        for version in (0, 1, True, 3):
            with self.assertRaises(CredentialError):
                rotate_password(blob, self.context, version, self.keys)

    def test_version_is_authenticated_even_when_two_versions_use_the_same_key(self):
        keys = {1: self.keys[1], 2: self.keys[1]}
        blob = encrypt_password('secret', self.context, keys)
        with self.assertRaises(CredentialError):
            decrypt_password(blob, dataclasses.replace(self.context, key_version=2), keys)
        with self.assertRaises(CredentialError):
            rotate_password(blob, self.context, 2, keys)

    def test_missing_invalid_or_short_keys_never_fall_back(self):
        for keys in ({}, {2: self.keys[2]}, {1: b'a' * 16}, {1: b'a' * 24},
                     {1: b'a' * 31}, {1: b'a' * 33}, {1: bytearray(32)}, {True: self.keys[1]}, None):
            with self.subTest(kind=type(keys).__name__):
                with self.assertRaises(CredentialError):
                    encrypt_password('secret', self.context, keys)

    def test_invalid_password_context_or_envelope_is_rejected(self):
        for password in ('', 'a' * 4097, 'é' * 2049, '\ud800', b'bytes', None):
            with self.assertRaises(CredentialError):
                encrypt_password(password, self.context, self.keys)
        for changes in ({'principal_id': 'not-uuid'}, {'binding_id': 'not-uuid'},
                        {'ch_username': ''}, {'ch_username': 'a' * 1025},
                        {'key_version': True}, {'key_version': 0}, {'key_version': 2**31}):
            with self.assertRaises(CredentialError):
                dataclasses.replace(self.context, **changes)
        for blob in (b'', b'a' * 28, b'a' * 4125, None, 'not-bytes', bytearray(32)):
            with self.assertRaises(CredentialError):
                decrypt_password(blob, self.context, self.keys)
        with self.assertRaises(CredentialError):
            encrypt_password('secret', None, self.keys)

    def test_structured_aad_cannot_confuse_separators_or_unicode_names(self):
        for name in ('alice|1|bob', 'a:b:c', 'pelanggan🔐', 'é', 'e\u0301'):
            context = dataclasses.replace(self.context, ch_username=name)
            blob = encrypt_password('secret', context, self.keys)
            self.assertEqual(decrypt_password(blob, context, self.keys), 'secret')
            with self.assertRaises(CredentialError):
                decrypt_password(blob, dataclasses.replace(context, ch_username=name + '|'), self.keys)


class KeyringLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / 'keys'
        self.directory.mkdir(mode=0o700)

    def key(self, name='1.key', data=None, mode=0o600):
        path = self.directory / name
        path.write_bytes(os.urandom(32) if data is None else data)
        path.chmod(mode)
        return path

    def test_loads_versioned_raw_keys_and_allows_owner_readonly(self):
        first = self.key()
        second = self.key('2.key', mode=0o400)
        self.assertEqual(load_keyring(self.directory), {1: first.read_bytes(), 2: second.read_bytes()})
        self.directory.chmod(0o500)
        self.assertEqual(len(load_keyring(self.directory)), 2)
        self.directory.chmod(0o700)

    def test_insecure_directory_or_file_modes_are_rejected(self):
        key = self.key()
        for mode in (0o644, 0o640, 0o660, 0o700, 0o1600):
            key.chmod(mode)
            with self.assertRaises(CredentialError):
                load_keyring(self.directory)
        key.chmod(0o600)
        for mode in (0o755, 0o750, 0o770):
            self.directory.chmod(mode)
            with self.assertRaises(CredentialError):
                load_keyring(self.directory)
        self.directory.chmod(0o700)

    def test_symlink_file_or_directory_is_rejected(self):
        actual = Path(self.temporary.name) / 'actual.key'
        actual.write_bytes(os.urandom(32))
        actual.chmod(0o600)
        (self.directory / '1.key').symlink_to(actual)
        with self.assertRaises(CredentialError):
            load_keyring(self.directory)
        (self.directory / '1.key').unlink()
        self.key()
        alias = Path(self.temporary.name) / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(CredentialError):
            load_keyring(alias)

    def test_unknown_filenames_aliases_empty_or_invalid_key_sizes_are_rejected(self):
        for name in ('0.key', '01.key', '2147483648.key', 'README.md', '.hidden', '-1.key', 'one.key'):
            path = self.key(name)
            with self.assertRaises(CredentialError):
                load_keyring(self.directory)
            path.unlink()
        for data in (b'', b'a' * 16, b'a' * 31, b'a' * 33, b'a' * 100000):
            path = self.key(data=data)
            with self.assertRaises(CredentialError):
                load_keyring(self.directory)
            path.unlink()
        with self.assertRaises(CredentialError):
            load_keyring(self.directory)

    def test_directory_and_fifo_entries_do_not_load_or_block(self):
        path = self.directory / '1.key'
        path.mkdir(mode=0o700)
        with self.assertRaises(CredentialError):
            load_keyring(self.directory)
        path.rmdir()
        os.mkfifo(path, 0o600)
        with self.assertRaises(CredentialError):
            load_keyring(self.directory)

    def test_wrong_owner_is_rejected_without_requiring_privileged_chown(self):
        self.key()
        with mock.patch('credential_crypto.os.geteuid', return_value=os.geteuid() + 1):
            with self.assertRaises(CredentialError):
                load_keyring(self.directory)


if __name__ == '__main__':
    unittest.main()
