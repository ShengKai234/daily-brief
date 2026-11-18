"""The only module that talks to py_webauthn (pinned in pyproject.toml).

Every check the design requires (docs/auth-passkey-design.md AUTH-3, 5.7) is explicit here
and does not rely on library defaults. The library is imported lazily to keep Lambda cold
starts small. Failures raise `WebAuthnFailure` with no detail: callers return one generic
error and never expose the cause.
"""

import json
from dataclasses import dataclass

from app.security import b64url_decode

ES256, RS256 = -7, -257
KNOWN_TRANSPORTS = {"usb", "nfc", "ble", "internal", "hybrid", "smart-card"}
REGISTRATION_FORMAT = "none"


class WebAuthnFailure(Exception):
    """Verification failed. Deliberately carries no cause."""


@dataclass(frozen=True)
class RegisteredCredential:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: str | None
    backup_eligible: bool
    backup_state: bool
    transports: list[str]


@dataclass(frozen=True)
class Assertion:
    credential_id: bytes
    sign_count: int
    user_handle: bytes | None
    backup_eligible: bool
    backup_state: bool


def creation_options(settings, *, challenge, user_id, user_name, display_name, exclude):
    from webauthn import generate_registration_options
    from webauthn.helpers import options_to_json_dict
    from webauthn.helpers.structs import (
        AttestationConveyancePreference,
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_name=user_name,
        user_id=user_id,
        user_display_name=display_name,
        challenge=challenge,
        timeout=300_000,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in exclude],
        supported_pub_key_algs=[ES256, RS256],
    )
    return options_to_json_dict(options)


def request_options(settings, *, challenge, allow):
    from webauthn import generate_authentication_options
    from webauthn.helpers import options_to_json_dict
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        challenge=challenge,
        timeout=300_000,
        allow_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in allow],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json_dict(options)


def _client_data(credential):
    """Reject cross-origin ceremonies explicitly: the library does not look at these fields."""
    try:
        raw = b64url_decode(credential["response"]["clientDataJSON"])
        data = json.loads(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise WebAuthnFailure from exc
    if not isinstance(data, dict):
        raise WebAuthnFailure
    if data.get("crossOrigin") not in (None, False) or "topOrigin" in data:
        raise WebAuthnFailure
    return data


def _check_flags(auth_flags_be, auth_flags_bs):
    if auth_flags_bs and not auth_flags_be:
        raise WebAuthnFailure


def verify_registration(settings, credential, challenge) -> RegisteredCredential:
    from webauthn import verify_registration_response
    from webauthn.helpers import parse_registration_credential_json
    from webauthn.helpers.exceptions import WebAuthnException

    _client_data(credential)
    try:
        parsed = parse_registration_credential_json(json.dumps(credential))
        verified = verify_registration_response(
            credential=parsed,
            expected_challenge=challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=list(settings.webauthn_origins),
            require_user_presence=True,
            require_user_verification=True,
            supported_pub_key_algs=[ES256, RS256],
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
        raise WebAuthnFailure from exc
    if str(getattr(verified.fmt, "value", verified.fmt)) != REGISTRATION_FORMAT:
        raise WebAuthnFailure
    be = verified.credential_device_type.value == "multi_device"
    bs = bool(verified.credential_backed_up)
    _check_flags(be, bs)
    raw_transports = credential["response"].get("transports")
    if raw_transports is None:
        raw_transports = []
    if not isinstance(raw_transports, list):
        raise WebAuthnFailure
    transports = [t for t in raw_transports if isinstance(t, str) and t in KNOWN_TRANSPORTS]
    aaguid = verified.aaguid
    if aaguid == "00000000-0000-0000-0000-000000000000":
        aaguid = None
    return RegisteredCredential(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        aaguid=aaguid,
        backup_eligible=be,
        backup_state=bs,
        transports=transports,
    )


def verify_assertion(settings, credential, challenge, public_key) -> Assertion:
    """Signature and context checks only. `current_sign_count` is always 0 (design 5.7), so the
    library can never reject on the counter before the signature is verified; the counter rule
    is applied afterwards by `apply_counter_rule`."""
    from webauthn import verify_authentication_response
    from webauthn.helpers import parse_authentication_credential_json
    from webauthn.helpers.exceptions import WebAuthnException

    _client_data(credential)
    try:
        parsed = parse_authentication_credential_json(json.dumps(credential))
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=list(settings.webauthn_origins),
            credential_public_key=public_key,
            credential_current_sign_count=0,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
        raise WebAuthnFailure from exc
    handle = parsed.response.user_handle
    return Assertion(
        credential_id=verified.credential_id,
        sign_count=verified.new_sign_count,
        user_handle=bytes(handle) if handle else None,
        backup_eligible=verified.credential_device_type.value == "multi_device",
        backup_state=bool(verified.credential_backed_up),
    )


def apply_counter_rule(stored: int, new: int, backup_eligible: bool) -> str:
    """Design 5.7. Returns 'ok', 'regression_allowed' or 'regression_denied'."""
    if stored == 0 and new == 0:
        return "ok"
    if new <= stored:
        return "regression_allowed" if backup_eligible else "regression_denied"
    return "ok"
