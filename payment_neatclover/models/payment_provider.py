# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.
# This module extends Odoo's payment framework.
# Odoo is a trademark of Odoo S.A.

import logging
import requests
from odoo.addons.payment_neatclover import const
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('neatclover', "Clover")], ondelete={'neatclover': 'set default'})
    
    neatclover_store_id = fields.Char(
        string="Store ID", help="Clover Store ID", 
        groups='base.group_system')
    neatclover_public_key = fields.Char(
        string="Public Key", help="Clover Public Key", required_if_provider='neatclover',
        groups='base.group_system')
    neatclover_private_key = fields.Char(
        string="Private Key", help="Clover Private Key",
        required_if_provider='neatclover')
    neatclover_activation_code = fields.Char(
        string="Activation Code", help="Contact us to receive a free activation code.")
    neatclover_cached_code = fields.Char(
        string="Cached Code", help="Cached Code")
    neatclover_use_iframe = fields.Boolean(string="Use iFrame", help="iFrame allows you to process the payment on your Odoo web page instead of being redirected to a Clover website. It provides a more seamless user experience.", default=False)
    neatclover_reset_code = fields.Boolean(string="Update Module Cache", help="If set to true it will update the module cache", default=False)
    
    def _default_neatclover_connection_url(self):
        """Set the default connection URL to the company's website URL."""
        return self.env.company.website or ""

    neatclover_connection_url = fields.Char(
        string="Odoo Connection URL",
        default=_default_neatclover_connection_url,
        help="Odoo URL used for payment communications",
        required_if_provider='neatclover',
        groups='base.group_system'
    )
    
    @api.model
    def _get_all_users(self):
        """Fetch all users and return them as selection options."""
        users = self.env['res.users'].search([])  # Get all users
        return [(str(user.id), user.name) for user in users]  # Store ID as string, show name

    neatclover_fallback_user_id = fields.Selection(
        selection=_get_all_users,
        string='Fallback Failure User',
        help='Select a user who will receive an activity if a transaction fails for a sale order that does not have a salesperson.'
    )

    def neatclover_get_code(self, activation_code):
        """ Get code. """
        if not self.company_id.website:
            raise ValidationError(_("Please enter the website on your company website."))
        try:
            headers = {
                "Referer": self.company_id.website,
                "Authorization": activation_code
            }
            response = requests.get(f"https://api.sns-software.com/api/AcquirerLicense/code?version={const.LICENSE_VERSION}", headers=headers, timeout=10)
            
            if response.status_code == 200:
                return response.text
                
            else:
                _logger.error(f"Failed to fetch activation code: {response.status_code} - {response.text}")
        except requests.RequestException as e:
            _logger.error(f"Request error: {e}")
        
        return None

    @api.model
    def create(self, vals):
        # Check if 'code' is 'neatclover' and activation code is being provided or changed
        _logger.info(f"neatclover_activation_code {vals.get('neatclover_activation_code')}")
        if vals.get('neatclover_activation_code'):
            _logger.info(f"old neatclover_activation_code {self.neatclover_activation_code}")
            if vals.get('neatclover_activation_code') != self.neatclover_activation_code or vals.get('neatclover_reset_code'):
                _logger.info(f"Before code")
                vals['neatclover_reset_code'] = False
                code = self.neatclover_get_code(vals['neatclover_activation_code'])
                _logger.info(f"Code: {code != None}")
                if code:
                    vals['neatclover_cached_code'] = code
                else:
                    _logger.info(f"Raised error for code")
                    raise ValidationError(_("The activation code is invalid. Please check and try again."))
        elif vals.get('neatclover_reset_code'):
            _logger.info(f"Before code")
            vals['neatclover_reset_code'] = False
            code = self.neatclover_get_code(self.neatclover_activation_code)
            _logger.info(f"Code: {code}")
            if code:
                vals['neatclover_cached_code'] = code
            else:
                _logger.info(f"Raised error for code")
                raise ValidationError(_("The activation code is invalid. Please check and try again."))
        return super(PaymentProvider, self).create(vals)

    def write(self, vals):
        # Check if 'code' is 'neatclover' and activation code is being updated
        _logger.info(f"neatclover_activation_code {vals.get('neatclover_activation_code')}")
        if vals.get('neatclover_activation_code'):
            _logger.info(f"old neatclover_activation_code {self.neatclover_activation_code}")
            if vals.get('neatclover_activation_code') != self.neatclover_activation_code or vals.get('neatclover_reset_code'):
                _logger.info(f"Before code")
                vals['neatclover_reset_code'] = False
                code = self.neatclover_get_code(vals['neatclover_activation_code'])
                _logger.info(f"Code: {code}")
                if code:
                    vals['neatclover_cached_code'] = code
                else:
                    _logger.info(f"Raised error for code")
                    raise ValidationError(_("The activation code is invalid. Please check and try again."))
        elif vals.get('neatclover_reset_code'):
            _logger.info(f"Before code")
            vals['neatclover_reset_code'] = False
            code = self.neatclover_get_code(self.neatclover_activation_code)
            _logger.info(f"Code: {code}")
            if code:
                vals['neatclover_cached_code'] = code
            else:
                _logger.info(f"Raised error for code")
                raise ValidationError(_("The activation code is invalid. Please check and try again."))
        return super(PaymentProvider, self).write(vals)

    def _compute_feature_support_fields(self):
        """ Override of `payment` to enable additional features. """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'neatclover').update({
            'support_manual_capture': 'partial',
            'support_refund': 'partial',
            'support_tokenization': False,
        })

    def _get_default_payment_method_codes(self):
        """ Override of `payment` to return the default payment method codes. """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'neatclover':
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES

    def neatclover_get_base_url(self):
        """Fiserv REST API host used for checkout creation and payment requests."""
        base_url = "https://prod.emea.api.fiservapps.com"
        if self.state == "test":
            base_url += "/sandbox"
        return base_url

    def neatclover_get_checkout_sdk_url(self, redirection_url=None):
        """Deprecated for VT — Fiserv VT uses checkout-lane hosted page, not Access Checkout SDK."""
        self.ensure_one()
        return self.neatclover_get_base_url()

    def neatclover_resolve_checkout_sdk_url(self, redirection_url=None):
        self.ensure_one()
        return self.neatclover_get_checkout_sdk_url(redirection_url)

