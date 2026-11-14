# -*- coding: utf-8 -*-
import logging
from datetime import datetime

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class NeatCloverFinalisePayment(models.Model):
    _name = 'neatclover.finalise.payment'
    _description = 'NeatClover Finalise Payment Queue'
    _order = 'id asc'
    _table = 'neatclover_finalise_payment'

    reference = fields.Char(required=True, index=True, copy=False)
    provider_id = fields.Many2one('payment.provider', required=True, index=True, ondelete='cascade')
    amount = fields.Float(required=True)
    currency_id = fields.Many2one('res.currency', required=True)
    device_type = fields.Char(required=True, default='ecommerce')
    state = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Pending'), ('sent', 'Sent')],
        default='draft',
        required=True,
        index=True,
    )

    def init(self):
        # Allow duplicate references; drop unique constraint from earlier versions.
        self.env.cr.execute(
            "ALTER TABLE neatclover_finalise_payment DROP CONSTRAINT IF EXISTS neatclover_finalise_payment_neatclover_finalise_reference_uniq"
        )

    @api.model
    def ensure_draft(self, reference, provider, amount, currency, device_type='ecommerce'):
        """Create a draft finalise row at payment init. Never raises."""
        try:
            if not reference or not provider or amount is None or not currency:
                _logger.info('NeatClover finalise ensure_draft skip (missing data) ref=%s', reference)
                return self.browse()
            existing = self.sudo().search([
                ('reference', '=', reference),
                ('provider_id', '=', provider.id),
                ('state', 'in', ('draft', 'pending')),
            ], order='id asc', limit=1)
            if existing:
                vals = {}
                if existing.state == 'draft':
                    if amount is not None and float(amount) != existing.amount:
                        vals['amount'] = float(amount)
                    if currency and existing.currency_id != currency:
                        vals['currency_id'] = currency.id
                    if device_type and existing.device_type != (device_type or 'ecommerce'):
                        vals['device_type'] = device_type or 'ecommerce'
                    if vals:
                        existing.write(vals)
                _logger.info('NeatClover finalise ensure_draft reuse ref=%s id=%s state=%s', reference, existing.id, existing.state)
                return existing
            rec = self.sudo().create({
                'reference': reference,
                'provider_id': provider.id,
                'amount': float(amount),
                'currency_id': currency.id,
                'device_type': device_type or 'ecommerce',
                'state': 'draft',
            })
            _logger.info('NeatClover finalise ensure_draft OK ref=%s id=%s', reference, rec.id)
            return rec
        except Exception:
            _logger.exception('NeatClover finalise ensure_draft failed ref=%s', reference)
            try:
                return self.sudo().search([
                    ('reference', '=', reference),
                    ('provider_id', '=', provider.id),
                    ('state', 'in', ('draft', 'pending')),
                ], order='id asc', limit=1)
            except Exception:
                return self.browse()

    @api.model
    def mark_pending(self, reference, provider, amount=None, currency=None, device_type=None):
        """Move draft -> pending when payment is done. Race-safe; never raises.

        Uses a short autonomous cursor+commit so a later SERIALIZATION_FAILURE
        on the parent request cannot roll back the pending flip.
        """
        try:
            if not reference or not provider:
                _logger.info('NeatClover finalise mark_pending skip (missing data) ref=%s', reference)
                return self.browse()
            table = self._table
            provider_id = provider.id
            amount_val = float(amount) if amount is not None else None
            currency_id = currency.id if currency else None
            device_val = device_type or None
            updated_id = None
            with self.env.registry.cursor() as cr:
                cr.execute(
                    """
                    SELECT id FROM %s
                     WHERE reference = %%s
                       AND provider_id = %%s
                       AND state = 'draft'
                     ORDER BY id
                     LIMIT 1
                     FOR UPDATE SKIP LOCKED
                    """ % table,
                    (reference, provider_id),
                )
                locked = cr.fetchone()
                if locked:
                    cr.execute(
                        """
                        UPDATE %s
                           SET state = 'pending',
                               amount = COALESCE(%%s, amount),
                               currency_id = COALESCE(%%s, currency_id),
                               device_type = COALESCE(%%s, device_type)
                         WHERE id = %%s AND state = 'draft'
                     RETURNING id
                        """ % table,
                        (amount_val, currency_id, device_val, locked[0]),
                    )
                    updated = cr.fetchone()
                    if updated:
                        updated_id = updated[0]
                cr.commit()
            if updated_id:
                _logger.info('NeatClover finalise mark_pending OK ref=%s id=%s', reference, updated_id)
                return self.browse(updated_id)
            # Already pending/sent, or locked by peer, or no draft — never create extras.
            existing = self.sudo().search([
                ('reference', '=', reference),
                ('provider_id', '=', provider.id),
                ('state', 'in', ('pending', 'sent')),
            ], order='id asc', limit=1)
            _logger.info(
                'NeatClover finalise mark_pending noop ref=%s existing=%s state=%s',
                reference, existing.id if existing else None, existing.state if existing else None,
            )
            return existing
        except Exception:
            _logger.exception('NeatClover finalise mark_pending failed ref=%s', reference)
            return self.browse()


    @api.model
    def enqueue(self, reference, provider, amount, currency, device_type='ecommerce'):
        """Backward-compatible: mark draft ready to send."""
        return self.mark_pending(reference, provider, amount, currency, device_type)

    @api.model
    def _cron_finalise_payment(self):
        """Every 24h: send all pending rows per provider (no reference dedupe)."""
        _logger.info('NeatClover finalise cron START')
        cr = self.env.cr
        table = self._table
        total_sent = 0

        cr.execute(
            "SELECT DISTINCT provider_id FROM %s WHERE state = 'pending'" % table
        )
        provider_ids = [row[0] for row in cr.fetchall()]
        if not provider_ids:
            _logger.info('NeatClover finalise cron no pending; DONE sent=0')
            return

        now = fields.Datetime.now()
        if isinstance(now, str):
            now = fields.Datetime.from_string(now)
        created_at_ms = int(now.timestamp() * 1000)
        day_start = datetime(now.year, now.month, now.day)
        date_ms = int(day_start.timestamp() * 1000)

        for provider_id in provider_ids:
            try:
                provider = self.env['payment.provider'].browse(provider_id).exists()
                _logger.info(
                    'NeatClover finalise cron provider id=%s state=%s',
                    provider.id if provider else None,
                    provider.state if provider else None,
                )
                if not provider or provider.state == 'disabled':
                    continue

                # All pending rows for this provider; aggregate only for API payload shape.
                cr.execute(
                    """
                    SELECT currency_id,
                           device_type,
                           COUNT(*) AS payment_count,
                           SUM(amount) AS amount
                    FROM %s
                    WHERE state = 'pending' AND provider_id = %%s
                    GROUP BY currency_id, device_type
                    """ % table,
                    (provider_id,),
                )
                aggregates = cr.fetchall()
                if not aggregates:
                    continue

                Currency = self.env['res.currency'].sudo()
                payloads = []
                for currency_id, device_type, payment_count, amount in aggregates:
                    currency = Currency.browse(currency_id)
                    if not currency or amount is None:
                        continue
                    payloads.append({
                        'developerMode': provider.state != 'enabled',
                        'deviceType': device_type or 'ecommerce',
                        'amount': float(amount),
                        'currency': currency.name,
                        'paymentCount': int(payment_count),
                        'createdAt': created_at_ms,
                        'date': date_ms,
                    })
                if not payloads:
                    continue

                cr.execute(
                    "SELECT id FROM %s WHERE state = 'pending' AND provider_id = %%s" % table,
                    (provider_id,),
                )
                pending_ids = [row[0] for row in cr.fetchall()]
                if not pending_ids:
                    continue

                exec_code = provider.neatclover_get_exec_code()
                if not exec_code:
                    _logger.warning('NeatClover finalise cron no exec code provider=%s', provider.id)
                    continue

                _logger.info(
                    'NeatClover finalise cron sending groups=%s rows=%s provider=%s',
                    len(payloads), len(pending_ids), provider.id,
                )
                local_context = {
                    'request_type': 'finalise_payment',
                    'developerMode': False,
                    'license_key': provider.neatclover_activation_code,
                    'finalise_payments': payloads,
                    'requests': requests,
                }
                exec(exec_code, {}, local_context)
                self.browse(pending_ids).sudo().write({'state': 'sent'})
                total_sent += len(pending_ids)
                _logger.info('NeatClover finalise cron sent OK provider=%s ids=%s', provider.id, pending_ids)
            except Exception:
                _logger.exception('NeatClover finalise cron send failed provider=%s', provider_id)

        _logger.info('NeatClover finalise cron DONE sent=%s', total_sent)
