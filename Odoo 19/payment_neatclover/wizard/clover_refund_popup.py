# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)
from odoo.addons.payment_neatclover.controllers.main import NeatCloverController


class CloverRefundPopup(models.TransientModel):
    _name = 'clover.refund.popup'
    _description = 'Clover Refund Popup'

    payment_link_id = fields.Many2one('clover.payment.link', readonly=True)
    transaction_id = fields.Many2one('payment.transaction', readonly=True)
    sale_order_id = fields.Many2one('sale.order', readonly=True)
    invoice_id = fields.Many2one('account.move', readonly=True)
    provider_id = fields.Many2one('payment.provider', readonly=True, required=True)
    currency_id = fields.Many2one('res.currency', readonly=True, required=True)
    ipg_transaction_id = fields.Char(readonly=True, required=True)
    amount_original = fields.Monetary(string='Original Amount', currency_field='currency_id', readonly=True)
    amount_refunded = fields.Monetary(string='Already Refunded', currency_field='currency_id', readonly=True)
    amount_available = fields.Monetary(string='Available to Refund', currency_field='currency_id', readonly=True)
    amount = fields.Monetary(string='Refund Amount', currency_field='currency_id', required=True)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        source = self._get_refund_source()
        vals.update(source)
        vals['amount'] = source.get('amount_available', 0.0)
        return vals

    @api.model
    def _compute_refund_balances(self, *, currency, ipg_transaction_id, document_amount,
                                 sale_order=None, invoice=None, gateway_amount=None):
        """Return original / refunded / available for this SO or invoice payment."""
        Refund = self.env['clover.refund']
        gateway_amount = gateway_amount if gateway_amount is not None else document_amount

        doc_domain = [('status', '=', 'done')]
        if sale_order:
            doc_domain.append(('sale_order_id', '=', sale_order.id))
        elif invoice:
            doc_domain.append(('invoice_id', '=', invoice.id))
        else:
            doc_domain.append(('ipg_transaction_id', '=', ipg_transaction_id))

        doc_refunds = Refund.search(doc_domain)
        # Also count older refunds that only have the IPG id (before sale_order_id/invoice_id existed)
        # when they belong to this same payment and no document was set.
        legacy_domain = [
            ('status', '=', 'done'),
            ('ipg_transaction_id', '=', ipg_transaction_id),
            ('sale_order_id', '=', False),
            ('invoice_id', '=', False),
        ]
        legacy_refunds = Refund.search(legacy_domain)
        doc_refunds |= legacy_refunds

        amount_refunded = sum(doc_refunds.mapped('amount'))
        doc_remaining = document_amount - amount_refunded

        gateway_refunds = Refund.search([
            ('status', '=', 'done'),
            ('ipg_transaction_id', '=', ipg_transaction_id),
        ])
        gateway_remaining = gateway_amount - sum(gateway_refunds.mapped('amount'))

        amount_available = min(doc_remaining, gateway_remaining)
        if currency.compare_amounts(amount_available, 0) <= 0:
            raise ValidationError(_('This payment has already been fully refunded.'))

        return {
            'amount_original': document_amount,
            'amount_refunded': amount_refunded,
            'amount_available': amount_available,
        }

    @api.model
    def _refund_values_from_payment(self, *, provider, currency, ipg_transaction_id, document_amount,
                                    payment_link=None, transaction=None, sale_order=None, invoice=None,
                                    gateway_amount=None):
        balances = self._compute_refund_balances(
            currency=currency,
            ipg_transaction_id=ipg_transaction_id,
            document_amount=document_amount,
            sale_order=sale_order,
            invoice=invoice,
            gateway_amount=gateway_amount,
        )
        return {
            'payment_link_id': payment_link.id if payment_link else False,
            'transaction_id': transaction.id if transaction else False,
            'sale_order_id': sale_order.id if sale_order else False,
            'invoice_id': invoice.id if invoice else False,
            'provider_id': provider.id,
            'currency_id': currency.id,
            'ipg_transaction_id': ipg_transaction_id,
            **balances,
        }

    @api.model
    def _get_refund_source(self):
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids') or []
        if active_model and not active_ids and self.env.context.get('active_id'):
            active_ids = [self.env.context['active_id']]
        if not active_model or not active_ids:
            raise ValidationError(_('No record selected for refund.'))
        if len(active_ids) != 1:
            raise ValidationError(_('Please select exactly one sales order or invoice to refund.'))

        active_id = active_ids[0]

        if active_model == 'sale.order':
            order = self.env['sale.order'].browse(active_id).exists()
            if not order:
                raise ValidationError(_('Sales order not found.'))

            # 1) Paid clover.payment.link (incl. multi pl/ links) — shared IPG on the link.
            links = self.env['clover.payment.link'].search([
                ('sale_order_ids', 'in', [order.id]),
                ('status', '=', 'paid'),
                ('neatclover_ipg_transaction_id', '!=', False),
            ], order='id desc', limit=1)
            if links:
                link = links
                gateway_amount = (
                    order.amount_total if len(link.sale_order_ids) == 1
                    else sum(link.sale_order_ids.mapped('amount_total'))
                )
                return self._refund_values_from_payment(
                    provider=link.provider_id,
                    currency=link.currency_id or order.currency_id,
                    ipg_transaction_id=link.neatclover_ipg_transaction_id,
                    document_amount=order.amount_total,
                    gateway_amount=gateway_amount,
                    payment_link=link,
                    sale_order=order,
                )

            # 2) No successful payment link — fall back to payment.transaction on the SO.
            txs = order.transaction_ids.filtered(
                lambda t: t.provider_code == 'neatclover'
                and t.state == 'done'
                and t.neatclover_ipg_transaction_id
            ).sorted(key=lambda t: t.id, reverse=True)
            matching_tx = txs.filtered(
                lambda t: order.currency_id.compare_amounts(t.amount, order.amount_total) == 0
            )[:1]
            tx = matching_tx or txs[:1]
            if tx:
                return self._refund_values_from_payment(
                    provider=tx.provider_id,
                    currency=tx.currency_id,
                    ipg_transaction_id=tx.neatclover_ipg_transaction_id,
                    document_amount=tx.amount,
                    gateway_amount=tx.amount,
                    transaction=tx,
                    sale_order=order,
                )

        elif active_model == 'account.move':
            invoice = self.env['account.move'].browse(active_id).exists()
            if not invoice:
                raise ValidationError(_('Invoice not found.'))

            # 1) Paid clover.payment.link first.
            links = self.env['clover.payment.link'].search([
                ('invoice_ids', 'in', [invoice.id]),
                ('status', '=', 'paid'),
                ('neatclover_ipg_transaction_id', '!=', False),
            ], order='id desc', limit=1)
            if links:
                link = links
                gateway_amount = (
                    invoice.amount_total if len(link.invoice_ids) == 1
                    else sum(link.invoice_ids.mapped('amount_total'))
                )
                return self._refund_values_from_payment(
                    provider=link.provider_id,
                    currency=link.currency_id or invoice.currency_id,
                    ipg_transaction_id=link.neatclover_ipg_transaction_id,
                    document_amount=invoice.amount_total,
                    gateway_amount=gateway_amount,
                    payment_link=link,
                    invoice=invoice,
                )

            # 2) Fall back to payment.transaction on the invoice.
            txs = self.env['payment.transaction'].search([
                ('invoice_ids', 'in', [invoice.id]),
                ('provider_code', '=', 'neatclover'),
                ('state', '=', 'done'),
                ('neatclover_ipg_transaction_id', '!=', False),
            ], order='id desc')
            matching_tx = txs.filtered(
                lambda t: invoice.currency_id.compare_amounts(t.amount, invoice.amount_total) == 0
            )[:1]
            tx = matching_tx or txs[:1]
            if tx:
                return self._refund_values_from_payment(
                    provider=tx.provider_id,
                    currency=tx.currency_id,
                    ipg_transaction_id=tx.neatclover_ipg_transaction_id,
                    document_amount=tx.amount,
                    gateway_amount=tx.amount,
                    transaction=tx,
                    invoice=invoice,
                )
        else:
            raise ValidationError(_('Refund is only available from sales orders or invoices.'))

        raise ValidationError(_('No paid Clover payment found for the selected record.'))

    def _get_live_available(self):
        """Recompute remaining refundable amount from DB (do not trust the form field)."""
        self.ensure_one()
        gateway_amount = self.amount_original
        if self.payment_link_id:
            link = self.payment_link_id
            if self.sale_order_id and len(link.sale_order_ids) > 1:
                gateway_amount = sum(link.sale_order_ids.mapped('amount_total'))
            elif self.invoice_id and len(link.invoice_ids) > 1:
                gateway_amount = sum(link.invoice_ids.mapped('amount_total'))
        elif self.transaction_id:
            gateway_amount = self.transaction_id.amount

        balances = self._compute_refund_balances(
            currency=self.currency_id,
            ipg_transaction_id=self.ipg_transaction_id,
            document_amount=self.amount_original,
            sale_order=self.sale_order_id,
            invoice=self.invoice_id,
            gateway_amount=gateway_amount,
        )
        return balances['amount_available']

    def action_refund(self):
        self.ensure_one()
        if self.currency_id.compare_amounts(self.amount, 0) <= 0:
            raise ValidationError(_('Refund amount must be greater than zero.'))

        available = self._get_live_available()
        if self.currency_id.compare_amounts(self.amount, available) > 0:
            raise ValidationError(
                _('Refund amount cannot exceed the available amount (%s).')
                % available
            )

        self._validate_gateway_transaction()

        payload = {
            'requestType': 'ReturnTransaction',
            'transactionAmount': {
                'total': f'{self.amount:.2f}',
                'currency': self.currency_id.name,
            },
        }
        if self.provider_id.neatclover_store_id:
            payload['storeId'] = self.provider_id.neatclover_store_id

        exec_code = self.provider_id.neatclover_cached_code
        if self.provider_id.neatclover_cached_code:
            exec_code = self.provider_id.neatclover_cached_code
        elif self.provider_id.neatclover_activation_code:
            exec_code = self.provider_id.neatclover_get_code(self.provider_id.neatclover_activation_code)
            if exec_code:
                self.provider_id.write({"neatclover_cached_code": exec_code})

        response = {}
        if exec_code:
            local_context = {"tr": self.transaction_id or self.payment_link_id, "processing_values": {}, 
                            "neat_clover_controller_result_action": NeatCloverController.result_action,
                            'env': self.env, 'fields': fields, "is_multi_payment_link": False, 'request_type': 'refund_payment_request_post', 'ipg_transaction_id': self.ipg_transaction_id, 'refund_payload': payload}
    
            exec(exec_code, {}, local_context)
            response =  local_context.get("response")

        result = (response.get('transactionResult') or response.get('transactionStatus') or '').upper()
        _logger.info(f'\n NeatClover Refund Result: {result} success={result == "APPROVED"} \n')
        if result != 'APPROVED':
            processor = response.get('processor') or {}
            detail = (
                processor.get('responseMessage')
                or response.get('errorMessage')
                or result
                or response.get('type')
                or _('Unknown error')
            )
            _logger.error(f'\n NeatClover Refund Failed: {detail} \n')
            raise ValidationError(_('Refund failed: %s') % detail)

        refund_ipg_id = response.get('ipgTransactionId')
        self.env['clover.refund'].create({
            'payment_link_id': self.payment_link_id.id or False,
            'transaction_id': self.transaction_id.id or False,
            'sale_order_id': self.sale_order_id.id or False,
            'invoice_id': self.invoice_id.id or False,
            'provider_id': self.provider_id.id,
            'currency_id': self.currency_id.id,
            'amount': self.amount,
            'ipg_transaction_id': self.ipg_transaction_id,
            'refund_ipg_transaction_id': refund_ipg_id,
            'status': 'done',
            'user_id': self.env.user.id,
        })
        self._post_refund_comment(refund_ipg_id)
        amount_str = f'{self.amount:.2f} {self.currency_id.name}'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Refund Successful'),
                'message': _('Clover refund of %s was completed successfully.') % amount_str,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _validate_gateway_transaction(self):
        """Confirm the original IPG transaction exists and is approved before refunding."""
        self.ensure_one()
        if not self.ipg_transaction_id:
            raise ValidationError(_('Missing Clover transaction id for refund.'))
        
        exec_code = self.provider_id.neatclover_cached_code
        if self.provider_id.neatclover_cached_code:
            exec_code = self.provider_id.neatclover_cached_code
        elif self.provider_id.neatclover_activation_code:
            exec_code = self.provider_id.neatclover_get_code(self.provider_id.neatclover_activation_code)
            if exec_code:
                self.provider_id.write({"neatclover_cached_code": exec_code})

        if exec_code:
            local_context = {"tr": self.transaction_id or self.payment_link_id, "processing_values": {}, 
                            "neat_clover_controller_result_action": NeatCloverController.result_action,
                            'env': self.env, 'fields': fields, "is_multi_payment_link": False, 'request_type': 'refund_payment_request', 'ipg_transaction_id': self.ipg_transaction_id}
    
            exec(exec_code, {}, local_context)
            tx_status =  local_context.get("response")
            _logger.info(f'\n NeatClover Refund Pre-check GET: {tx_status} \n')

            remote_ipg = tx_status.get('ipgTransactionId')
            result = (tx_status.get('transactionResult') or tx_status.get('transactionStatus') or '').upper()
            if not remote_ipg or str(remote_ipg) != str(self.ipg_transaction_id) or result != 'APPROVED':
                detail = (
                    (tx_status.get('processor') or {}).get('responseMessage')
                    or tx_status.get('errorMessage')
                    or result
                    or tx_status.get('type')
                    or _('transaction not found')
                )
                _logger.error(
                    f'\n NeatClover Refund Pre-check Failed: ipg={self.ipg_transaction_id} '
                    f'remote_ipg={remote_ipg} result={result} detail={detail} \n'
                )
                raise ValidationError(
                    _('Cannot refund: original Clover transaction is missing or not approved (%s).')
                    % detail
                )

    def _post_refund_comment(self, refund_ipg_id=None):
        self.ensure_one()
        record = self.sale_order_id or self.invoice_id
        if not record:
            active_model = self.env.context.get('active_model')
            active_id = self.env.context.get('active_id') or (self.env.context.get('active_ids') or [None])[0]
            if active_model and active_id:
                record = self.env[active_model].browse(active_id).exists()
        if not record or not hasattr(record, 'message_post'):
            return

        amount_str = f'{self.amount:.2f} {self.currency_id.name}'
        body = _(
            'Clover refund of %(amount)s completed by %(user)s.'
            ' Original IPG: %(ipg)s. Refund IPG: %(refund_ipg)s.'
        ) % {
            'amount': amount_str,
            'user': self.env.user.name,
            'ipg': self.ipg_transaction_id or '-',
            'refund_ipg': refund_ipg_id or '-',
        }
        record.message_post(
            body=body,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
