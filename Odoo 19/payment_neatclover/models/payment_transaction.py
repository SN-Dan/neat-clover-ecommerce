# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.
# This module extends Odoo's payment framework.
# Odoo is a trademark of Odoo S.A.

import logging
import uuid

import requests
from odoo import api, _, fields, models
from odoo.addons.payment_neatclover.controllers.main import NeatCloverController

_logger = logging.getLogger(__name__)
from odoo.addons.payment_neatclover.const import RESPONSE_CODES_MAPPING


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # neatclover_session_id = fields.Char()
    neatclover_checkout_id = fields.Char()
    neatclover_ipg_transaction_id = fields.Char()
    neatclover_payment_identifier = fields.Char(copy=False)
    neatclover_failure_activity_scheduled = fields.Boolean(
        string='Failure Activity Scheduled',
        default=False,
        copy=False,
        help='Set when a failure activity has been scheduled for this transaction.',
    )

    def _neatclover_logger(self, message):
        _logger.info(f"{message}")

    def _get_specific_processing_values(self, processing_values):
        """Injects clover-specific values into the payment form."""
        self.ensure_one()
        if self.provider_code != "neatclover":
            return super()._get_specific_processing_values(processing_values)

        exec_code = self.provider_id.neatclover_get_exec_code()

        if exec_code:
            now_str = fields.Datetime.now().strftime('%Y%m%d%H%M%S')
            random_number = self.neatclover_payment_identifier or uuid.uuid5(
                uuid.NAMESPACE_DNS, f"clover_payment-{now_str}-{self.id}"
            ).hex
            base_url = (
                self.provider_id.neatclover_connection_url
                or self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                or self.provider_id.get_base_url()
            )
            odoo_redirect_url, webhook_url = self.env['neatclover.url.token'].sudo().mint_checkout_urls(
                base_url, random_number, self.amount, self.currency_id,
                result_path=NeatCloverController.result_action,
            )
            local_context = {
                "tr": self,
                "processing_values": processing_values,
                "neat_clover_controller_result_action": NeatCloverController.result_action,
                "env": self.env,
                "fields": fields,
                "is_multi_payment_link": False,
                "request_type": "take_payment_request",
                "random_number": random_number,
                "odoo_redirect_url": odoo_redirect_url,
                "webhook_url": webhook_url,
            }
            exec(exec_code, {}, local_context)
            response = local_context.get("response")
            checkout_url = response.get('checkout', {}).get('redirectionUrl')
            self.write({'neatclover_payment_identifier': local_context.get('random_number'),
                        'neatclover_checkout_id': response.get("checkout", {}).get('checkoutId')})

            if checkout_url:
                try:
                    self.env['neatclover.finalise.payment'].sudo().ensure_draft(
                        self.reference,
                        self.provider_id,
                        self.amount,
                        self.currency_id,
                        self._neatclover_device_type_for_finalise(),
                    )
                except Exception:
                    pass
            return {
                "payment_url": checkout_url,
                "transactionNotificationURL": local_context.get("odoo_redirect_url"),
                "neatclover_use_iframe": self.provider_id.neatclover_use_iframe,
            }
        return {'payment_url': False,
                'transactionNotificationURL': False,
                'neatclover_use_iframe': False}

    def _extract_amount_data(self, payment_data):
        """Override of payment to extract the amount and currency from the payment data."""
        if self.provider_code != 'neatclover':
            return super()._extract_amount_data(payment_data)
        approved = (payment_data or {}).get('approvedAmount') or {}
        if not approved.get('total') or not approved.get('currency'):
            # e.g. WAITING/3DS webhook — skip amount check; final webhook has approvedAmount.
            return None
        return {
            'amount': approved['total'],
            'currency_code': approved['currency'],
            'precision_digits': self.currency_id.decimal_places,
        }

    def _apply_updates(self, payment_data):
        """Override of payment to update the transaction based on the payment data.

        Note: self.ensure_one()

        :param dict payment_data: The payment data sent by the provider
        :return: None
        """
        if self.provider_code != 'neatclover':
            return super()._apply_updates(payment_data)

        self.write({
            'provider_reference': payment_data.get('ipgTransactionDetails', {}).get('processor', {}).get('referenceNumber', False),
            'neatclover_ipg_transaction_id': payment_data.get('ipgTransactionDetails', {}).get('ipgTransactionId', False),
        })

        payment_method = self.env['payment.method']._get_from_code('neatclover')
        self.payment_method_id = payment_method or self.payment_method_id

        state = payment_data['transactionStatus']
        _logger.info(f"\n Process State {state} \n")
        if state in RESPONSE_CODES_MAPPING['done']:
            self._set_done()
        elif state in RESPONSE_CODES_MAPPING['pending']:
            self._set_pending()
        elif state in RESPONSE_CODES_MAPPING['authorised']:
            self._set_authorized()
        elif state in RESPONSE_CODES_MAPPING['error']:
            self._set_error("Payment declined.")

    def _neatclover_device_type_for_finalise(self):
        self.ensure_one()
        device_type = 'ecommerce'
        try:
            if 'clover.payment.link' in self.env:
                link = self.env['clover.payment.link'].sudo().search(
                    [('reference', '=', self.reference)], limit=1
                )
                if link and getattr(link, 'neatclover_vt_wizard_id', False):
                    device_type = 'virtual terminal'
        except Exception:
            pass
        return device_type

    def write(self, vals):
        # Stash previous states for constrains; no business logic here.
        if 'state' in vals:
            prev = dict(self.env.context.get('neat_payment_prev_states') or {})
            for tx in self:
                prev.setdefault(tx.id, tx.state)
            self = self.with_context(neat_payment_prev_states=prev)
        return super().write(vals)

    @api.constrains('state')
    def _neatclover_on_state_done(self):
        """Queue finalise when state changes to done (previous state must not be done)."""
        prev_states = self.env.context.get('neat_payment_prev_states') or {}
        Finalise = self.env['neatclover.finalise.payment'].sudo()
        for tx in self.filtered(
            lambda t: t.provider_code == 'neatclover'
            and t.state == 'done'
            and t.id in prev_states
            and prev_states.get(t.id) != 'done'
        ):
            try:
                Finalise.mark_pending(
                    tx.reference,
                    tx.provider_id,
                    tx.amount,
                    tx.currency_id,
                    tx._neatclover_device_type_for_finalise(),
                )
            except Exception:
                pass
