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
    is_partial = fields.Boolean(string='Partial Payment', default=False, index=True)
    partial_amount = fields.Monetary(string='Partial Amount', currency_field='currency_id')
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
        'invoice_ids', 'invoice_ids.amount_residual', 'invoice_ids.amount_total',
        'invoice_ids.currency_id', 'invoice_ids.partner_id',
        'sale_order_ids', 'sale_order_ids.amount_total', 'sale_order_ids.currency_id', 'sale_order_ids.partner_id',
        'is_partial', 'partial_amount', 'status',
    )
    def _compute_payment_values(self):
        for rec in self:
            if rec.sale_order_ids:
                rec.currency_id = rec.sale_order_ids[:1].currency_id
                rec.partner_id = rec.sale_order_ids[:1].partner_id
                full_total = sum(rec.sale_order_ids.mapped('amount_total'))
                if rec.is_partial and len(rec.sale_order_ids) == 1:
                    rec.amount_total = rec._get_sale_order_remaining_amount(
                        rec.sale_order_ids[:1], exclude_link=rec,
                    )
                else:
                    rec.amount_total = full_total
            else:
                rec.currency_id = rec.invoice_ids[:1].currency_id
                rec.partner_id = rec.invoice_ids[:1].partner_id
                # After pay, residuals are 0 — keep charged amount for paid full invoice links
                # so refunds (and UI) do not treat the payment as a zero charge.
                if rec.status == 'paid' and not rec.is_partial:
                    rec.amount_total = sum(rec.invoice_ids.mapped('amount_total'))
                else:
                    rec.amount_total = sum(rec.invoice_ids.mapped('amount_residual'))
            if rec.is_partial:
                rec.amount = rec.partial_amount
            else:
                rec.amount = rec.amount_total

    @api.model
    def _paid_partial_domain(self, sale_order=None, invoice=None, exclude_link=None):
        domain = [('is_partial', '=', True), ('status', '=', 'paid')]
        if sale_order:
            domain.append(('sale_order_ids', 'in', [sale_order.id]))
        elif invoice:
            domain.append(('invoice_ids', 'in', [invoice.id]))
        else:
            return [('id', '=', 0)]
        if exclude_link:
            domain.append(('id', '!=', exclude_link.id))
        return domain

    @api.model
    def document_has_partial_payment(self, sale_order=None, invoice=None):
        return bool(self.sudo().search(self._paid_partial_domain(sale_order=sale_order, invoice=invoice), limit=1))

    @api.model
    def _get_sale_order_remaining_amount(self, sale_order, exclude_link=None):
        sale_order = sale_order[:1]
        invoices = sale_order.invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
        )
        if invoices:
            return sale_order.currency_id.round(sum(invoices.mapped('amount_residual')))
        paid = sum(self.sudo().search(
            self._paid_partial_domain(sale_order=sale_order, exclude_link=exclude_link)
        ).mapped('amount'))
        return sale_order.currency_id.round(sale_order.amount_total - paid)

    def _assert_documents_allow_multi_payment(self, sale_orders=None, invoices=None):
        """Block multi link/VT once a document already has a paid partial VT payment."""
        from odoo.exceptions import ValidationError
        from odoo import _

        for order in (sale_orders or self.env['sale.order']):
            if self.document_has_partial_payment(sale_order=order):
                raise ValidationError(_(
                    'Sales order %s already has a Clover partial VT payment. '
                    'Use "Partial Pay by Clover VT" for further payments.'
                ) % order.name)
        for invoice in (invoices or self.env['account.move']):
            if self.document_has_partial_payment(invoice=invoice):
                raise ValidationError(_(
                    'Invoice %s already has a Clover partial VT payment. '
                    'Use "Partial Pay by Clover VT" for further payments.'
                ) % (invoice.name or invoice.display_name))

    def _prepare_partial_amount(self, amount):
        self.ensure_one()
        from odoo.exceptions import ValidationError
        from odoo import _

        currency = self.currency_id
        if currency.compare_amounts(amount, 0) <= 0:
            raise ValidationError(_('Payment amount must be greater than zero.'))
        if currency.compare_amounts(amount, self.amount_total) > 0:
            raise ValidationError(_('Payment amount cannot exceed the remaining due amount.'))
        # Store the chosen amount and drop any previous checkout built with another amount.
        self.write({
            'partial_amount': amount,
            'neatclover_checkout_id': False,
            'neatclover_payment_url': False,
            'neatclover_payment_identifier': False,
        })
        return amount

    def _get_checkout_amount(self):
        self.ensure_one()
        return self.partial_amount if self.is_partial else self.amount

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
                    "amount": self.partial_amount if self.is_partial else self.amount,
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
                "is_partial": bool(self.is_partial),
                "vt_transaction_origin": origin,
                'request_type': 'take_payment_request',
            }
            exec(exec_code, local_context)
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

    def _get_sale_orders_payment_transaction(self):
        self.ensure_one()
        return self.env['payment.transaction'].sudo().search([
            ('reference', '=', self.reference),
        ], limit=1)

    def _create_sale_orders_payment_transaction(self):
        """Create payment.transaction for SO payment-link/VT using the joint reference."""
        self.ensure_one()
        if not self.sale_order_ids:
            return self.env['payment.transaction']
        existing = self._get_sale_orders_payment_transaction()
        if existing:
            return existing

        vals = {
            'provider_id': self.provider_id.id,
            'reference': self.reference,
            'amount': self._get_checkout_amount(),
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'operation': 'online_direct',
            'sale_order_ids': [(6, 0, self.sale_order_ids.ids)],
        }
        payment_method = self.env['payment.method'].sudo().search([
            ('code', '=', self.provider_id.code),
        ], limit=1)
        if payment_method:
            vals['payment_method_id'] = payment_method.id
        return self.env['payment.transaction'].sudo().create(vals)

    def _run_sale_orders_payment_transaction_post_process(self, tx):
        # Default finalize disabled — use register payment flow instead.
        # tx._finalize_post_processing()
        return True

    def _register_document_payments(self, invoices):
        """Create payments via account.payment.register (same path for invoices / multi-SO)."""
        if not invoices:
            return self.env['account.payment']
        wizard_ctx = {
            'active_model': 'account.move',
            'active_ids': invoices.ids,
            'active_id': invoices.ids[0],
        }
        register_vals = {}
        if self.provider_id.journal_id:
            register_vals['journal_id'] = self.provider_id.journal_id.id
        if getattr(self, 'is_partial', False):
            register_vals['amount'] = self.amount
            register_vals['group_payment'] = False
        else:
            register_vals['group_payment'] = True
        wizard = self.env['account.payment.register'].sudo().with_context(**wizard_ctx).create(register_vals)
        return wizard._create_payments()

    def _complete_invoices_payment(self):
        invoices = self.invoice_ids.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')
        if invoices:
            self._register_document_payments(invoices)
        return True

    def _complete_multi_sale_orders_like_invoices(self, tx):
        """Confirm/invoice SOs if needed, then register onto unpaid invoices (same as invoice flow)."""
        for order in self.sale_order_ids.filtered(lambda o: o.state in ('draft', 'sent')):
            order.with_context(send_email=True).action_confirm()

        orders = self.sale_order_ids.filtered(lambda o: o.state == 'sale')
        if not orders:
            tx.is_post_processed = True
            return True

        # Further partials: pay residual on existing invoices (do not rely on _create_invoices).
        unpaid = orders.mapped('invoice_ids').filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice' and m.payment_state != 'paid'
        )
        if not unpaid:
            orders._force_lines_to_invoice_policy_order()
            invoices = orders.with_context(raise_if_nothing_to_invoice=False)._create_invoices(final=True)
            draft_invoices = invoices.filtered(lambda m: m.state == 'draft')
            if draft_invoices:
                draft_invoices.action_post()
            unpaid = invoices.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')

        if unpaid:
            payments = self._register_document_payments(unpaid)
            if payments and not tx.payment_id:
                tx.payment_id = payments[:1].id
            tx.invoice_ids = [(6, 0, unpaid.ids)]

        tx.is_post_processed = True
        return True

    def _complete_sale_orders_payment_transaction(self):
        self.ensure_one()
        if self.sale_order_ids:
            tx = self._get_sale_orders_payment_transaction()
            if not tx:
                return False
            if tx.state != 'done':
                tx._set_done()
            if tx.is_post_processed:
                return True
            # Finalize disabled — always use register payment flow.
            # self._run_sale_orders_payment_transaction_post_process(tx)
            return self._complete_multi_sale_orders_like_invoices(tx)
        if self.invoice_ids:
            return self._complete_invoices_payment()
        return False

    def _cancel_sale_orders_payment_transaction(self):
        self.ensure_one()
        tx = self._get_sale_orders_payment_transaction()
        if not tx or tx.state in ('done', 'cancel'):
            return False
        tx._set_canceled()
        return True
