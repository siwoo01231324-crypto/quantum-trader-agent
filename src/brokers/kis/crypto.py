from __future__ import annotations

import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding


def decrypt_aes256_cbc_pkcs7(ciphertext_b64: str, key_b64: str, iv_b64: str) -> str:
    """KIS WS 체결통보 AES-256-CBC + PKCS7 복호화.

    Args:
        ciphertext_b64: Base64 인코딩된 암호문
        key_b64: Base64 인코딩된 AES 키 (구독 응답 body.output.key)
        iv_b64: Base64 인코딩된 IV (구독 응답 body.output.iv)

    Returns:
        복호화된 평문 문자열 (UTF-8)
    """
    key = base64.b64decode(key_b64)
    iv = base64.b64decode(iv_b64)
    ciphertext = base64.b64decode(ciphertext_b64)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    plaintext_bytes = unpadder.update(padded) + unpadder.finalize()
    return plaintext_bytes.decode("utf-8")
