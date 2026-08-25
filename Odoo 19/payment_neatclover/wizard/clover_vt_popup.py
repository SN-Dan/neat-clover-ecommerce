# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CloverVTPopup(models.TransientModel):
    _name = 'clover.vt.popup'
    _description = 'Clover Virtual Terminal Popup'

    provider_id = fields.Many2one('payment.provider', string='Payment Provider', required=True)
    virtual_payment_id = fields.Many2one('clover.payment.link', string='Virtual Payment', readonly=True)
    reference = fields.Char(string='Reference', readonly=True)
    payment_page_html = fields.Html(string='Payment', sanitize=False, compute='_compute_payment_page_html')
    transaction_reference = fields.Char(string='Transaction Reference', readonly=True)
    transaction_key = fields.Char(string='Transaction Key', readonly=True)
    checkout_id = fields.Char(string='Checkout ID', readonly=True)
    clover_url = fields.Char(string='Clover URL', readonly=True)
    checkout_sdk_url = fields.Char(string='Checkout SDK URL', readonly=True)
    payment_url = fields.Char(string='Fiserv Checkout URL', readonly=True)
    transaction_origin = fields.Selection(
        [('MAIL', 'Mail'), ('PHONE', 'Phone')],
        string='Origin',
        default=False,
        required=False,
        help='Sent to Fiserv as transactionOrigin (available in exec_code as vt_transaction_origin).',
    )

    @api.depends('provider_id', 'virtual_payment_id', 'checkout_id', 'payment_url')
    def _compute_payment_page_html(self):
        for rec in self:
            rec.payment_page_html = False
            if rec.id:
                page_url = (
                    f'/neatclovervt/invoice_payment/{rec.id}/pay'
                    if rec.checkout_id and rec.payment_url
                    else f'/neatclovervt/invoice_payment/{rec.id}'
                )
                # Compact dialog: narrower width, enough height so content fits without scroll.
                iframe_style = (
                    'width: 100%; min-height: 400px; height: 440px; border: 0;'
                    if rec.checkout_id and rec.payment_url
                    else 'width: 100%; min-height: 380px; height: 420px; border: 0;'
                )
                rec.payment_page_html = (
                    '<iframe id="neatclovervt-wizard-iframe" '
                    f'src="{page_url}" '
                    f'style="{iframe_style}" '
                    'scrolling="yes" '
                    'tabindex="0" '
                    'allow="payment *" '
                    '/>'
                )

    @api.model
    def _create_vt_wizard_from_virtual_payment(self, virtual_payment, provider):
        return self.sudo().create({
            'provider_id': provider.id,
            'virtual_payment_id': virtual_payment.id,
            'reference': virtual_payment.reference,
            'transaction_reference': virtual_payment.reference,
            'transaction_key': '',
            'checkout_id': '',
            'transaction_origin': False,
            'clover_url': provider.neatclover_get_base_url() or '',
        })

    @api.model
    def create_from_invoices(self, invoices):
        invoices = invoices.sudo().exists()
        invoices = invoices.filtered(lambda m: m.is_invoice(include_receipts=False) and m.state == 'posted')
        if not invoices:
            raise ValidationError(_('Please select at least one posted customer invoice.'))
        if any(inv.payment_state == 'paid' for inv in invoices):
            raise ValidationError(_('One or more selected invoices are already paid.'))
        if len(invoices.mapped('currency_id')) > 1:
            raise ValidationError(_('All selected invoices must have the same currency.'))
        if len(invoices.mapped('partner_id')) > 1:
            raise ValidationError(_('All selected invoices must belong to the same customer.'))

        provider = self.env['payment.provider'].sudo().search([
            ('code', '=', 'neatclover'),
            ('state', '!=', 'disabled'),
        ], limit=1)
        if not provider:
            raise ValidationError(_('Clover virtual terminal provider is not configured.'))

        virtual_payment = self.env['clover.payment.link'].sudo().create({
            'provider_id': provider.id,
            'status': 'draft',
            'invoice_ids': [(6, 0, invoices.ids)],
        })
        wizard = self._create_vt_wizard_from_virtual_payment(virtual_payment, provider)
        virtual_payment.sudo().write({'neatclover_vt_wizard_id': wizard.id})
        return wizard

    @api.model
    def create_from_orders(self, orders):
        orders = orders.sudo().exists()
        if not orders:
            raise ValidationError(_('Please select at least one sales order.'))
        not_quotation = orders.filtered(lambda o: o.state not in ('draft', 'sent'))
        if not_quotation:
            raise ValidationError(_(
                'Clover payment is only available for quotations (Draft or Quotation sent).'
            ))
        orders = orders.filtered(lambda o: o.state in ('draft', 'sent'))
        if len(orders.mapped('partner_id')) > 1:
            raise ValidationError(_('All selected orders must belong to the same customer.'))
        if len(orders.mapped('currency_id')) > 1:
            raise ValidationError(_('All selected orders must use the same currency.'))

        provider = self.env['payment.provider'].sudo().search([
            ('code', '=', 'neatclover'),
            ('state', '!=', 'disabled'),
        ], limit=1)
        if not provider:
            raise ValidationError(_('Clover virtual terminal provider is not configured.'))

        virtual_payment = self.env['clover.payment.link'].sudo().create({
            'provider_id': provider.id,
            'status': 'draft',
            'sale_order_ids': [(6, 0, orders.ids)],
        })
        wizard = self._create_vt_wizard_from_virtual_payment(virtual_payment, provider)
        virtual_payment.sudo().write({'neatclover_vt_wizard_id': wizard.id})
        return wizard
