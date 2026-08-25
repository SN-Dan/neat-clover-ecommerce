# -*- coding: utf-8 -*-
from odoo import fields, models


class CloverRefund(models.Model):
    _name = 'clover.refund'
    _description = 'Clover Refund'
    _order = 'create_date desc'

    payment_link_id = fields.Many2one('clover.payment.link', string='Payment Link', index=True, ondelete='set null')
    transaction_id = fields.Many2one('payment.transaction', string='Payment Transaction', index=True, ondelete='set null')
    sale_order_id = fields.Many2one('sale.order', string='Sales Order', index=True, ondelete='set null')
    invoice_id = fields.Many2one('account.move', string='Invoice', index=True, ondelete='set null')
    provider_id = fields.Many2one('payment.provider', string='Payment Provider', required=True, index=True)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True)
    amount = fields.Monetary(string='Refund Amount', currency_field='currency_id', required=True)
    ipg_transaction_id = fields.Char(string='Original IPG Transaction ID', required=True, index=True)
    refund_ipg_transaction_id = fields.Char(string='Refund IPG Transaction ID')
    status = fields.Selection([
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='Status', required=True, default='done', index=True)
    user_id = fields.Many2one('res.users', string='Refunded By', default=lambda self: self.env.user, required=True)
