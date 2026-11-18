"""A software WebAuthn authenticator for tests: test keys only, no device, no network."""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass
class FakeAuthenticator:
    rp_id: str = "localhost"
    origin: str = "http://localhost:5173"
    backup_eligible: bool = False
    backup_state: bool = False
    sign_count: int = 0
    counts: bool = True  # False models synced passkeys, whose counter stays 0
    credential_id: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )

    def cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def flags(self, up=True, uv=True, at=False, be=None, bs=None):
        be = self.backup_eligible if be is None else be
        bs = self.backup_state if bs is None else bs
        return (
            (UP if up else 0)
            | (UV if uv else 0)
            | (BE if be else 0)
            | (BS if bs else 0)
            | (AT if at else 0)
        )

    def auth_data(self, *, rp_id=None, attested=False, count=0, **flag_kwargs):
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        data = rp_hash + bytes([self.flags(at=attested, **flag_kwargs)]) + count.to_bytes(4, "big")
        if attested:
            data += (
                bytes(16)
                + len(self.credential_id).to_bytes(2, "big")
                + self.credential_id
                + self.cose_key()
            )
        return data

    def client_data(self, kind, challenge, origin=None, **extra):
        return json.dumps(
            {
                "type": kind,
                "challenge": b64(challenge),
                "origin": origin or self.origin,
                "crossOrigin": False,
                **extra,
            }
        ).encode()

    def create(self, public_key, *, origin=None, rp_id=None, challenge=None, fmt="none", **kw):
        """Answer navigator.credentials.create() for the server's creation options."""
        challenge = challenge or unb64(public_key["challenge"])
        client_data = self.client_data(
            "webauthn.create", challenge, origin, **kw.pop("client_extra", {})
        )
        auth_data = self.auth_data(rp_id=rp_id, attested=True, count=0, **kw)
        attestation = cbor2.dumps({"fmt": fmt, "attStmt": {}, "authData": auth_data})
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "attestationObject": b64(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(
        self,
        public_key,
        user_handle,
        *,
        origin=None,
        rp_id=None,
        challenge=None,
        count=None,
        drop_handle=False,
        bad_signature=False,
        client_extra=None,
        **kw,
    ):
        """Answer navigator.credentials.get(); `count` overrides the incrementing counter."""
        challenge = challenge or unb64(public_key["challenge"])
        if count is None:
            if self.counts:
                self.sign_count += 1
            count = self.sign_count
        client_data = self.client_data("webauthn.get", challenge, origin, **(client_extra or {}))
        auth_data = self.auth_data(rp_id=rp_id, count=count, **kw)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        if bad_signature:
            signature = signature[:-1] + bytes([signature[-1] ^ 1])
        response = {
            "clientDataJSON": b64(client_data),
            "authenticatorData": b64(auth_data),
            "signature": b64(signature),
        }
        if not drop_handle:
            response["userHandle"] = b64(user_handle)
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
        }
