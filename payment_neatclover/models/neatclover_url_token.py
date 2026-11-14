# -*- coding: utf-8 -*-
"""Short AES-GCM URL tokens for Clover redirect (24h) and webhook (48h)."""
import base64
import hashlib
import logging
import os
import struct
import time
from urllib.parse import urljoin

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from odoo import api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

TOKEN_VERSION = 1
PURPOSE_REDIRECT = 1
PURPOSE_WEBHOOK = 2
PURPOSE_NAMES = {
    PURPOSE_REDIRECT: 'redirect',
    PURPOSE_WEBHOOK: 'webhook',
}
TTL_REDIRECT_SECONDS = 24 * 3600
TTL_WEBHOOK_SECONDS = 48 * 3600
_KEY_PREFIX = b'neatclover-url-v1'


class NeatCloverUrlToken(models.AbstractModel):
    _name = 'neatclover.url.token'
    _description = 'NeatClover Encrypted URL Token'

    @api.model
    def _database_secret(self):
        return (self.env['ir.config_parameter'].sudo().get_param('database.secret') or '').encode()

    @api.model
    def _aes_key(self):
        return hashlib.sha256(_KEY_PREFIX + self._database_secret()).digest()

    @api.model
    def amount_to_minor_units(self, amount, currency):
        digits = currency.decimal_places if currency else 2
        return int(round(float(amount) * (10 ** digits)))

    @api.model
    def seal(self, purpose, reference, amount, currency, ttl_seconds=None):
        """Return base64url token (no padding). purpose: 'redirect'|'webhook'|int."""
        if isinstance(purpose, str):
            purpose = PURPOSE_REDIRECT if purpose == 'redirect' else PURPOSE_WEBHOOK
        if purpose not in (PURPOSE_REDIRECT, PURPOSE_WEBHOOK):
            raise ValidationError('Invalid NeatClover URL token purpose.')
        reference = (reference or '').encode('utf-8')
        if not reference or len(reference) > 255:
            raise ValidationError('Invalid NeatClover URL token reference.')
        if ttl_seconds is None:
            ttl_seconds = (
                TTL_REDIRECT_SECONDS if purpose == PURPOSE_REDIRECT else TTL_WEBHOOK_SECONDS
            )
        currency_code = ((currency.name if currency else 'XXX') or 'XXX')[:3].ljust(3).encode('ascii')
        amount_cents = self.amount_to_minor_units(amount, currency)
        exp = int(time.time()) + int(ttl_seconds)
        plaintext = struct.pack(
            '>BBIq3sB',
            TOKEN_VERSION,
            purpose,
            exp,
            amount_cents,
            currency_code,
            len(reference),
        ) + reference
        nonce = os.urandom(12)
        token = AESGCM(self._aes_key()).encrypt(nonce, plaintext, None)
        return base64.urlsafe_b64encode(nonce + token).decode('ascii').rstrip('=')

    @api.model
    def open(self, token, expected_purpose=None):
        """Decrypt token. Returns dict or raises ValidationError."""
        if not token:
            raise ValidationError('Missing NeatClover URL token.')
        raw = token.encode('ascii') if isinstance(token, str) else token
        pad = b'=' * (-len(raw) % 4)
        try:
            blob = base64.urlsafe_b64decode(raw + pad)
        except Exception as exc:
            raise ValidationError('Invalid NeatClover URL token encoding.') from exc
        if len(blob) < 12 + 16 + 1:
            raise ValidationError('Invalid NeatClover URL token.')
        nonce, ciphertext = blob[:12], blob[12:]
        try:
            plaintext = AESGCM(self._aes_key()).decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise ValidationError('Invalid NeatClover URL token.') from exc
        if len(plaintext) < 1 + 1 + 4 + 8 + 3 + 1:
            raise ValidationError('Invalid NeatClover URL token payload.')
        version, purpose, exp, amount_cents, currency_code, id_len = struct.unpack(
            '>BBIq3sB', plaintext[:18]
        )
        if version != TOKEN_VERSION:
            raise ValidationError('Unsupported NeatClover URL token version.')
        if expected_purpose is not None:
            expected = (
                PURPOSE_REDIRECT if expected_purpose == 'redirect'
                else PURPOSE_WEBHOOK if expected_purpose == 'webhook'
                else expected_purpose
            )
            if purpose != expected:
                raise ValidationError('NeatClover URL token purpose mismatch.')
        if int(time.time()) > int(exp):
            raise ValidationError('NeatClover URL token expired.')
        reference = plaintext[18:18 + id_len].decode('utf-8')
        if len(plaintext) != 18 + id_len:
            raise ValidationError('Invalid NeatClover URL token payload length.')
        return {
            'purpose': PURPOSE_NAMES.get(purpose, purpose),
            'purpose_code': purpose,
            'exp': exp,
            'amount_cents': amount_cents,
            'currency': currency_code.decode('ascii').strip(),
            'reference': reference,
        }

    @api.model
    def mint_checkout_urls(self, base_url, reference, amount, currency, result_path='/neatclover/result'):
        """Build redirect (24h) and webhook (48h) URLs with sealed tokens."""
        base = (base_url or '').rstrip('/') + '/'
        redirect_token = self.seal('redirect', reference, amount, currency, TTL_REDIRECT_SECONDS)
        webhook_token = self.seal('webhook', reference, amount, currency, TTL_WEBHOOK_SECONDS)
        redirect_url = urljoin(base, result_path.lstrip('/'))
        redirect_url = f"{redirect_url.rstrip('/')}?p={redirect_token}"
        webhook_url = urljoin(base, f"neatclover/wh/{webhook_token}")
        return redirect_url, webhook_url

    @api.model
    def record_amount_matches(self, claims, amount, currency, tolerance_cents=0):
        """True when sealed amount/currency match the record."""
        if not claims or amount is None or not currency:
            return False
        if (currency.name or '').upper() != (claims.get('currency') or '').upper():
            return False
        expected = self.amount_to_minor_units(amount, currency)
        return abs(int(expected) - int(claims['amount_cents'])) <= int(tolerance_cents)
