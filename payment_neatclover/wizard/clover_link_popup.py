# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CloverLinkPopup(models.TransientModel):
    _name = 'clover.link.popup'
    _description = 'Clover Link Popup'

    provider_id = fields.Many2one(
        'payment.provider',
        string='Payment Provider',
        required=True,
        domain="[('code', '=', 'neatclover'), ('state', '!=', 'disabled')]",
    )
    reference = fields.Char(string='Reference', readonly=True)
    payment_link = fields.Char(string='Payment Link', readonly=True)

    def _build_link_payload(self, link_rec):
        payload_data = {
            'link_id': link_rec.id,
            'reference': link_rec.reference,
            'provider_id': link_rec.provider_id.id,
        }
        payload_json = json.dumps(payload_data, separators=(',', ':')).encode()
        encoded_payload = base64.urlsafe_b64encode(payload_json).decode().rstrip('=')
        secret = (self.env['ir.config_parameter'].sudo().get_param('database.secret') or '').encode()
        signature = hmac.new(secret, encoded_payload.encode(), hashlib.sha256).hexdigest()
        return f'{encoded_payload}.{signature}'

    def _generate_link_values(self, provider=None):
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids', [])
        link_vals = {
            'status': 'draft',
        }
        if active_model == 'sale.order':
            orders = self.env['sale.order'].sudo().browse(active_ids).exists()
            if not orders:
                raise ValidationError('Please select at least one sales order.')
            not_quotation = orders.filtered(lambda o: o.state not in ('draft', 'sent'))
            if not_quotation:
                raise ValidationError(
                    'Clover payment is only available for quotations (Draft or Quotation sent).'
                )
            orders = orders.filtered(lambda o: o.state in ('draft', 'sent'))
            if len(orders.mapped('partner_id')) > 1:
                raise ValidationError('All selected orders must belong to the same customer.')
            if len(orders.mapped('currency_id')) > 1:
                raise ValidationError('All selected orders must use the same currency.')
            link_vals['sale_order_ids'] = [(6, 0, orders.ids)]
        else:
            invoices = self.env['account.move'].sudo().browse(active_ids).exists()
            invoices = invoices.filtered(lambda m: m.is_invoice(include_receipts=False) and m.state == 'posted')
            if not invoices:
                raise ValidationError('Please select at least one posted customer invoice.')
            if any(inv.payment_state == 'paid' for inv in invoices):
                raise ValidationError('One or more selected invoices are already paid.')
            if len(invoices.mapped('currency_id')) > 1:
                raise ValidationError('All selected invoices must have the same currency.')
            if len(invoices.mapped('partner_id')) > 1:
                raise ValidationError('All selected invoices must belong to the same customer.')
            link_vals['invoice_ids'] = [(6, 0, invoices.ids)]

        provider = provider or self.env['payment.provider'].sudo().search([
            ('code', '=', 'neatclover'),
            ('state', '!=', 'disabled'),
        ], limit=1)
        if not provider or not provider.neatclover_connection_url:
            raise ValidationError('Clover provider connection URL is not configured.')

        link_vals['provider_id'] = provider.id
        link_rec = self.env['clover.payment.link'].sudo().create(link_vals)
        payload = self._build_link_payload(link_rec)
        # base_url = provider.neatclover_connection_url.rstrip('/')
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url').rstrip('/')
        return {
            'provider_id': provider.id,
            'reference': link_rec.reference,
            'payment_link': f'{base_url}/neatclover/payment_link/{payload}',
        }

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        provider = self.env['payment.provider'].sudo().search([('code', '=', 'neatclover'), ('state', '!=', 'disabled')], limit=1)
        vals.update({'provider_id': provider.id})
        return vals

    @api.onchange('provider_id')
    def _onchange_provider_id(self):
        if self.provider_id:
            values = self._generate_link_values(self.provider_id.sudo())
            self.reference = values.get('reference')
            self.payment_link = values.get('payment_link')
