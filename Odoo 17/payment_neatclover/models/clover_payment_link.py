# -*- coding: utf-8 -*-
import base64
import logging
import re
import requests
import uuid
from decimal import Decimal

from werkzeug import urls

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CloverPaymentLink(models.Model):
    _name = 'clover.payment.link'
    _description = 'Clover Payment Link'
    _rec_name = 'reference'

    def get_default_reference_value(self):
        now_str = fields.Datetime.now().strftime('%Y%m%d%H%M%S%f')
        unique_seed = f"clover_payment-{now_str}"
        return f"pl/{uuid.uuid5(uuid.NAMESPACE_DNS, unique_seed).hex[:27]}"

    reference = fields.Char(string='Reference', required=True, default=get_default_reference_value, index=True)
    provider_id = fields.Many2one('payment.provider', string='Payment Provider', required=True, index=True)
    company_id = fields.Many2one('res.company', string='Company', related='provider_id.company_id', store=True, readonly=True)
    partner_id = fields.Many2one('res.partner', string='Customer', compute='_compute_payment_values', store=True)
    status = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('cancel', 'Cancelled'),
        ('error', 'Error'),
    ], string='Status', required=True, default='draft', index=True)
    invoice_ids = fields.Many2many('account.move', string='Invoices')
    sale_order_ids = fields.Many2many('sale.order', string='Sales Orders')
    currency_id = fields.Many2one('res.currency', string='Currency', compute='_compute_payment_values', store=True)
    amount_total = fields.Monetary(string='Total Amount', currency_field='currency_id', compute='_compute_payment_values', store=True)
    amount = fields.Monetary(string='Amount', currency_field='currency_id', compute='_compute_payment_values', store=True)

    neatclover_checkout_id = fields.Char()
    neatclover_payment_url = fields.Char(copy=False)
    neatclover_ipg_transaction_id = fields.Char()
    neatclover_payment_identifier = fields.Char(copy=False)
    neatclover_vt_wizard_id = fields.Integer(copy=False)
    neatclover_failure_activity_scheduled = fields.Boolean(
        string='Failure Activity Scheduled',
        default=False,
        copy=False,
        help='Set when a failure activity has been scheduled for this payment link.',
    )

    @api.depends(
        'invoice_ids', 'invoice_ids.amount_residual', 'invoice_ids.currency_id', 'invoice_ids.partner_id',
        'sale_order_ids', 'sale_order_ids.amount_total', 'sale_order_ids.currency_id', 'sale_order_ids.partner_id',
    )
    def _compute_payment_values(self):
        for rec in self:
            if rec.sale_order_ids:
                rec.currency_id = rec.sale_order_ids[:1].currency_id
                rec.partner_id = rec.sale_order_ids[:1].partner_id
                rec.amount_total = sum(rec.sale_order_ids.mapped('amount_total'))
            else:
                rec.currency_id = rec.invoice_ids[:1].currency_id
                rec.partner_id = rec.invoice_ids[:1].partner_id
                rec.amount_total = sum(rec.invoice_ids.mapped('amount_residual'))
            rec.amount = rec.amount_total

    _sql_constraints = [
        ('clover_payment_link_reference_uniq', 'unique(reference)', 'Clover payment link reference must be unique.'),
    ]

    def _neatclover_logger(self, message):
        _logger.info(f"{message}")

    def neatclover_get_processing_values(self, provider=None, result_action=None, transaction_origin=None, force_new=False):
        self.ensure_one()
        provider = provider or self.provider_id
        origin = (transaction_origin or 'ECOM')
        if isinstance(origin, str):
            origin = origin.upper().strip()

        if (
            not force_new
            and self.neatclover_checkout_id
            and self.neatclover_payment_url
            and self.neatclover_payment_identifier
        ):
            checkout_sdk_url = provider.neatclover_resolve_checkout_sdk_url(self.neatclover_payment_url)
            return {
                "payment_url": self.neatclover_payment_url,
                "checkout_sdk_url": checkout_sdk_url,
                "checkout_id": self.neatclover_checkout_id,
                "transaction_key": self.neatclover_payment_identifier,
                "transaction_reference": self.reference,
                "transactionNotificationURL": False,
                "neatclover_use_iframe": self.provider_id.neatclover_use_iframe,
            }

        exec_code = None
        if provider.neatclover_cached_code:
            exec_code = provider.neatclover_cached_code
        elif provider.neatclover_activation_code:
            exec_code = provider.neatclover_get_code(provider.neatclover_activation_code)
            if exec_code:
                provider.write({"neatclover_cached_code": exec_code})

        if exec_code:
            local_context = {
                "tr": self,
                "processing_values": {
                    "reference": self.reference,
                    "amount": self.amount,
                    "currency_id": self.currency_id.id,
                    "partner_id": self.partner_id.id,
                },
                "Decimal": Decimal,
                "requests": requests,
                "base64": base64,
                "re": re,
                "urls": urls,
                "neat_clover_controller_result_action": result_action,
                "env": self.env,
                "fields": fields,
                "is_multi_payment_link": True,
                "vt_transaction_origin": origin,
                'request_type': 'take_payment_request',
            }
            exec(exec_code, {}, local_context)
            response = local_context.get("response") or {}
            checkout_url = response.get('checkout', {}).get('redirectionUrl')
            checkout_sdk_url = provider.neatclover_resolve_checkout_sdk_url(checkout_url)
            self.write({
                'neatclover_payment_identifier': local_context.get('random_number'),
                'neatclover_checkout_id': response.get("checkout", {}).get('checkoutId'),
                'neatclover_payment_url': checkout_url,
            })

            checkout_id = response.get('checkout', {}).get('checkoutId') or self.neatclover_checkout_id
            transaction_key = local_context.get('random_number') or self.neatclover_payment_identifier
            return {
                "payment_url": checkout_url,
                "checkout_sdk_url": checkout_sdk_url,
                "checkout_id": checkout_id,
                "transaction_key": transaction_key,
                "transaction_reference": self.reference,
                "transactionNotificationURL": local_context.get("odoo_redirect_url"),
                "neatclover_use_iframe": self.provider_id.neatclover_use_iframe,
            }
        return {
            'payment_url': False,
            'checkout_sdk_url': provider.neatclover_get_checkout_sdk_url(),
            'checkout_id': False,
            'transaction_key': False,
            'transaction_reference': self.reference,
            'transactionNotificationURL': False,
            'neatclover_use_iframe': False,
        }
