"""
macOS Chrome cookie / credential decryption.

Chrome on macOS stores an AES key derived from a password retrieved via
the system Keychain ('Chrome Safe Storage'). This module handles that
derivation and the AES-CBC decryption of v10-prefixed values.
"""
import logging
import sys

log = logging.getLogger(__name__)

_SALT = b'saltysalt'
_IV = b' ' * 16
_KEY_LEN = 16
_ITERATIONS = 1003


def _derive_key(password: str) -> bytes:
    from Cryptodome.Protocol.KDF import PBKDF2
    return PBKDF2(password.encode('utf-8'), _SALT, _KEY_LEN, _ITERATIONS)


def _aes_decrypt(data: bytes, key: bytes) -> bytes:
    from Cryptodome.Cipher import AES
    cipher = AES.new(key, AES.MODE_CBC, IV=_IV)
    decrypted = cipher.decrypt(data)
    # Remove PKCS7 padding
    pad_len = decrypted[-1]
    return decrypted[:-pad_len]


class MacDecryptor:
    """
    Lazy-initialised decryptor. Fetches the Keychain password once and
    caches the derived AES key for the lifetime of the object.
    """

    def __init__(self):
        if sys.platform != 'darwin':
            raise RuntimeError('MacDecryptor is only supported on macOS')
        self._key: bytes | None = None

    def _ensure_key(self):
        if self._key is not None:
            return
        try:
            import keyring
            password = keyring.get_password('Chrome Safe Storage', 'Chrome')
            if password is None:
                raise ValueError('Chrome Safe Storage password not found in Keychain')
            self._key = _derive_key(password)
        except Exception as e:
            raise RuntimeError(f'Could not retrieve Chrome decryption key: {e}') from e

    def decrypt(self, encrypted_value: bytes) -> str | None:
        """
        Decrypt a Chrome-encrypted cookie/credential value.

        Returns the plaintext string, or None if decryption fails or the
        value is not encrypted (no v10 prefix).
        """
        if not encrypted_value or len(encrypted_value) < 3:
            return None

        prefix = encrypted_value[:3]
        if prefix not in (b'v10', b'v11'):
            # Not an AES-encrypted value — return as-is if it's text
            try:
                return encrypted_value.decode('utf-8')
            except (UnicodeDecodeError, AttributeError):
                return None

        try:
            self._ensure_key()
            decrypted = _aes_decrypt(encrypted_value[3:], self._key)
            return decrypted.decode('utf-8', errors='replace')
        except Exception as e:
            log.debug(f'Decryption failed: {e}')
            return '<encrypted>'
