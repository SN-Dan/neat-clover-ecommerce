# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.
# This module extends Odoo's payment framework.
# Odoo is a trademark of Odoo S.A.

import logging
from odoo import _, fields, models
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

        exec_code = None
        if self.provider_id.neatclover_cached_code:
            exec_code = self.provider_id.neatclover_cached_code
        elif self.provider_id.neatclover_activation_code:
            exec_code = self.provider_id.neatclover_get_code(self.provider_id.neatclover_activation_code)
            if exec_code:
                self.provider_id.write({"neatclover_cached_code": exec_code})

        if exec_code:
            local_context = {"tr": self, "processing_values": processing_values, 
                            "neat_clover_controller_result_action": NeatCloverController.result_action,
                            'env': self.env, 'fields': fields, "is_multi_payment_link": False, 'request_type': 'take_payment_request'}
            exec(exec_code, {}, local_context)
            response = local_context.get("response")
            checkout_url = response.get('checkout', {}).get('redirectionUrl')
            self.write({'neatclover_payment_identifier': local_context.get('random_number'),
                        'neatclover_checkout_id': response.get("checkout", {}).get('checkoutId')})
            return {
                "payment_url": checkout_url,
                "transactionNotificationURL": local_context.get("odoo_redirect_url"),
                "neatclover_use_iframe": self.provider_id.neatclover_use_iframe,
            }
        return {'payment_url': False,
                'transactionNotificationURL': False,
                'neatclover_use_iframe': False}

    def _process_notification_data(self, notification_data):
        """ Override of payment to process the transaction based on dummy data.

        Note: self.ensure_one()

        :param dict notification_data: The dummy notification data
        :return: None
        :raise: ValidationError if inconsistent data were received
        """
        super()._process_notification_data(notification_data)
        if self.provider_code != 'neatclover':
            return
        
        self.write({'provider_reference': notification_data.get('ipgTransactionDetails', {}).get('processor', {}).get('referenceNumber', False),
                    'neatclover_ipg_transaction_id': notification_data.get('ipgTransactionDetails', {}).get('ipgTransactionId', False)})

        payment_method = self.env['payment.method']._get_from_code('neatclover')

        self.payment_method_id = payment_method or self.payment_method_id

        state = notification_data['transactionStatus']
        _logger.info(f"\n Process State {state} \n")
        if state in RESPONSE_CODES_MAPPING['done']:
            self._set_done()
        elif state in RESPONSE_CODES_MAPPING["pending"]:
            self._set_pending()
        elif state in RESPONSE_CODES_MAPPING["authorised"]:
            self._set_authorized()
        elif state in RESPONSE_CODES_MAPPING["error"]:
            self._set_error("Payment declined.")