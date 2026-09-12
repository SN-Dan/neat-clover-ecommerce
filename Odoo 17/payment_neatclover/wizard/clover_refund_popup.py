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
    refund_plan_info = fields.Char(
        string='Payment references',
        readonly=True,
        help='Clover gateway charges that can be refunded (waterfall, same idea as POS).',
    )

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        source = self._get_refund_source()
        vals.update(source)
        vals['amount'] = source.get('amount_available', 0.0)
        return vals

    @api.model
    def _paid_links_for_document(self, sale_order=None, invoice=None):
        domain = [
            ('status', '=', 'paid'),
            ('neatclover_ipg_transaction_id', '!=', False),
        ]
        if sale_order:
            domain.append(('sale_order_ids', 'in', [sale_order.id]))
        elif invoice:
            domain.append(('invoice_ids', 'in', [invoice.id]))
        else:
            return self.env['clover.payment.link']
        return self.env['clover.payment.link'].search(domain, order='id asc')

    @api.model
    def _link_charged_amount(self, link):
        """Amount actually charged on this link's Fiserv transaction.

        Invoice links recompute amount_total from amount_residual, which is 0 once
        paid — prefer the payment.transaction amount, partial_amount, or invoice
        totals so refunds still see the real gateway charge.
        """
        if link.is_partial:
            return link.partial_amount or link.amount
        tx = link._get_sale_orders_payment_transaction()
        if tx:
            return tx.amount
        charged = link.amount_total or link.amount
        if not charged and link.invoice_ids:
            return sum(link.invoice_ids.mapped('amount_total'))
        return charged

    @api.model
    def _link_refund_remaining(self, link, currency):
        charged = self._link_charged_amount(link)
        refunded = sum(self.env['clover.refund'].search([
            ('status', '=', 'done'),
            ('ipg_transaction_id', '=', link.neatclover_ipg_transaction_id),
        ]).mapped('amount'))
        return currency.round(charged - refunded)

    @api.model
    def _tx_refund_remaining(self, tx, currency):
        refunded = sum(self.env['clover.refund'].search([
            ('status', '=', 'done'),
            ('ipg_transaction_id', '=', tx.neatclover_ipg_transaction_id),
        ]).mapped('amount'))
        return currency.round(tx.amount - refunded)

    @api.model
    def _iter_document_charges(self, sale_order=None, invoice=None):
        """All Clover ecom/VT gateway charges on the document (oldest first).

        Same idea as POS get_refunds(): one entry per payment reference / IPG.
        """
        charges = []
        seen_ipg = set()

        links = self._paid_links_for_document(sale_order=sale_order, invoice=invoice)
        for link in links:
            ipg = link.neatclover_ipg_transaction_id
            if not ipg or ipg in seen_ipg:
                continue
            seen_ipg.add(ipg)
            charges.append({
                'payment_link': link,
                'transaction': self.env['payment.transaction'],
                'ipg': ipg,
                'charged': self._link_charged_amount(link),
            })

        txs = self.env['payment.transaction']
        if sale_order:
            txs = sale_order.transaction_ids.filtered(
                lambda t: t.provider_code == 'neatclover'
                and t.state == 'done'
                and t.neatclover_ipg_transaction_id
            ).sorted(key=lambda t: t.id)
        elif invoice:
            txs = self.env['payment.transaction'].search([
                ('invoice_ids', 'in', [invoice.id]),
                ('provider_code', '=', 'neatclover'),
                ('state', '=', 'done'),
                ('neatclover_ipg_transaction_id', '!=', False),
            ], order='id asc')
        for tx in txs:
            ipg = tx.neatclover_ipg_transaction_id
            if not ipg or ipg in seen_ipg:
                continue
            seen_ipg.add(ipg)
            charges.append({
                'payment_link': self.env['clover.payment.link'],
                'transaction': tx,
                'ipg': ipg,
                'charged': tx.amount,
            })
        return charges

    @api.model
    def _document_cap_for_charge(self, charge, sale_order=None, invoice=None):
        """Max refundable on this gateway charge for the selected SO/invoice.

        Multi-doc link/VT (one charge covering several SOs/invoices): cap to that
        document's amount. Single-doc / partial charges keep the charged amount.
        """
        gateway_charged = charge['charged']
        link = charge.get('payment_link')
        tx = charge.get('transaction')
        if sale_order:
            if (link and len(link.sale_order_ids) > 1) or (tx and len(tx.sale_order_ids) > 1):
                return sale_order.amount_total
            return gateway_charged
        if invoice:
            if (link and len(link.invoice_ids) > 1) or (tx and len(tx.invoice_ids) > 1):
                return invoice.amount_total
            return gateway_charged
        return gateway_charged

    @api.model
    def _charge_remaining(self, charge, currency, sale_order=None, invoice=None):
        """Gateway remaining, capped by what this document may still refund."""
        if charge.get('payment_link'):
            gateway_remaining = self._link_refund_remaining(charge['payment_link'], currency)
        else:
            gateway_remaining = self._tx_refund_remaining(charge['transaction'], currency)

        doc_cap = self._document_cap_for_charge(charge, sale_order=sale_order, invoice=invoice)
        refund_domain = [
            ('status', '=', 'done'),
            ('ipg_transaction_id', '=', charge['ipg']),
        ]
        if sale_order:
            refund_domain.append(('sale_order_id', '=', sale_order.id))
        elif invoice:
            refund_domain.append(('invoice_id', '=', invoice.id))
        else:
            return gateway_remaining
        doc_refunded = sum(self.env['clover.refund'].search(refund_domain).mapped('amount'))
        doc_remaining = currency.round(doc_cap - doc_refunded)
        return currency.round(min(gateway_remaining, doc_remaining))

    @api.model
    def _refund_values_from_charges(self, charges, sale_order=None, invoice=None):
        """UI balances across all Clover payment references (POS-style)."""
        if not charges:
            raise ValidationError(_('No paid Clover payment found for the selected record.'))

        first = charges[0]
        provider = (first['payment_link'] or first['transaction']).provider_id
        currency = (first['payment_link'] or first['transaction']).currency_id
        if sale_order:
            currency = currency or sale_order.currency_id
        elif invoice:
            currency = currency or invoice.currency_id

        amount_original = currency.round(sum(
            self._document_cap_for_charge(c, sale_order=sale_order, invoice=invoice)
            for c in charges
        ))
        remainings = [
            self._charge_remaining(c, currency, sale_order=sale_order, invoice=invoice)
            for c in charges
        ]
        amount_available = currency.round(sum(max(r, 0.0) for r in remainings))
        amount_refunded = currency.round(amount_original - amount_available)
        if currency.compare_amounts(amount_available, 0) <= 0:
            raise ValidationError(_('This payment has already been fully refunded.'))

        refundable = [
            (c, r) for c, r in zip(charges, remainings)
            if currency.compare_amounts(r, 0) > 0
        ]
        plan = '; '.join(
            _('%(ipg)s (%(left).2f left)') % {'ipg': c['ipg'], 'left': r}
            for c, r in refundable
        )
        anchor = refundable[0][0]
        return {
            'payment_link_id': anchor['payment_link'].id if anchor['payment_link'] else False,
            'transaction_id': anchor['transaction'].id if anchor['transaction'] else False,
            'sale_order_id': sale_order.id if sale_order else False,
            'invoice_id': invoice.id if invoice else False,
            'provider_id': provider.id,
            'currency_id': currency.id,
            'ipg_transaction_id': anchor['ipg'],
            'amount_original': amount_original,
            'amount_refunded': amount_refunded,
            'amount_available': amount_available,
            'refund_plan_info': _('%(count)s payment(s): %(plan)s') % {
                'count': len(refundable),
                'plan': plan,
            },
        }

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
    def _refund_values_from_partial_links(self, links, sale_order=None, invoice=None):
        """Aggregate refundable balances across multiple partial gateway charges."""
        currency = links[:1].currency_id
        if sale_order:
            currency = currency or sale_order.currency_id
        elif invoice:
            currency = currency or invoice.currency_id
        provider = links[:1].provider_id

        amount_original = currency.round(sum(self._link_charged_amount(link) for link in links))
        amount_available = currency.round(sum(
            max(self._link_refund_remaining(link, currency), 0.0) for link in links
        ))
        amount_refunded = currency.round(amount_original - amount_available)
        if currency.compare_amounts(amount_available, 0) <= 0:
            raise ValidationError(_('This payment has already been fully refunded.'))

        return {
            'payment_link_id': links.id if len(links) == 1 else False,
            'transaction_id': False,
            'sale_order_id': sale_order.id if sale_order else False,
            'invoice_id': invoice.id if invoice else False,
            'provider_id': provider.id,
            'currency_id': currency.id,
            # Anchor IPG for the form; action_refund may waterfall across several.
            'ipg_transaction_id': links[:1].neatclover_ipg_transaction_id,
            'amount_original': amount_original,
            'amount_refunded': amount_refunded,
            'amount_available': amount_available,
        }

    @api.model
    def _sale_order_is_fully_paid_for_refund(self, order):
        """True when posted invoices are paid, or uninvoiced SO is fully covered by paid Clover links/txs."""
        currency = order.currency_id
        invoices = order.invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
        )
        if invoices:
            return all(currency.compare_amounts(inv.amount_residual, 0) <= 0 for inv in invoices)

        paid_links = self.env['clover.payment.link'].sudo().search([
            ('sale_order_ids', 'in', [order.id]),
            ('status', '=', 'paid'),
        ])
        if paid_links:
            paid = sum(paid_links.mapped('amount'))
            return currency.compare_amounts(paid, order.amount_total) >= 0

        txs = order.transaction_ids.filtered(
            lambda t: t.provider_code == 'neatclover' and t.state == 'done'
        )
        if txs:
            return currency.compare_amounts(sum(txs.mapped('amount')), order.amount_total) >= 0
        return False

    @api.model
    def _assert_document_fully_paid_for_refund(self, sale_order=None, invoice=None):
        msg = _(
            'This document is not fully paid. '
            'Refunds for partially paid sales orders or invoices should be done through the portal. '
            'A credit note for the refund has to be created manually.'
        )
        if invoice:
            if invoice.currency_id.compare_amounts(invoice.amount_residual, 0) > 0:
                raise ValidationError(msg)
            return
        if sale_order and not self._sale_order_is_fully_paid_for_refund(sale_order):
            raise ValidationError(msg)

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
            self._assert_document_fully_paid_for_refund(sale_order=order)
            charges = self._iter_document_charges(sale_order=order)
            if charges:
                return self._refund_values_from_charges(charges, sale_order=order)

        elif active_model == 'account.move':
            invoice = self.env['account.move'].browse(active_id).exists()
            if not invoice:
                raise ValidationError(_('Invoice not found.'))
            self._assert_document_fully_paid_for_refund(invoice=invoice)
            charges = self._iter_document_charges(invoice=invoice)
            if charges:
                return self._refund_values_from_charges(charges, invoice=invoice)
        else:
            raise ValidationError(_('Refund is only available from sales orders or invoices.'))

        raise ValidationError(_('No paid Clover payment found for the selected record.'))

    def _refund_targets(self):
        """Yield (payment_link, transaction, ipg_transaction_id, remaining) — POS-style waterfall."""
        self.ensure_one()
        currency = self.currency_id
        charges = []
        if self.sale_order_id:
            charges = self._iter_document_charges(sale_order=self.sale_order_id)
        elif self.invoice_id:
            charges = self._iter_document_charges(invoice=self.invoice_id)
        if not charges and self.payment_link_id:
            charges = [{
                'payment_link': self.payment_link_id,
                'transaction': self.env['payment.transaction'],
                'ipg': self.payment_link_id.neatclover_ipg_transaction_id,
                'charged': self._link_charged_amount(self.payment_link_id),
            }]
        if not charges and self.transaction_id and self.ipg_transaction_id:
            charges = [{
                'payment_link': self.env['clover.payment.link'],
                'transaction': self.transaction_id,
                'ipg': self.ipg_transaction_id,
                'charged': self.transaction_id.amount,
            }]

        for charge in charges:
            remaining = self._charge_remaining(
                charge,
                currency,
                sale_order=self.sale_order_id,
                invoice=self.invoice_id,
            )
            if currency.compare_amounts(remaining, 0) > 0:
                yield (
                    charge['payment_link'],
                    charge['transaction'],
                    charge['ipg'],
                    remaining,
                )

    def _get_live_available(self):
        """Recompute remaining refundable amount from DB (do not trust the form field)."""
        self.ensure_one()
        return self.currency_id.round(
            sum(remaining for _link, _tx, _ipg, remaining in self._refund_targets())
        )

    def _refund_exec_tr(self, payment_link=None, transaction=None):
        """Record the cached script uses as `tr` (must expose .provider_id)."""
        tr = payment_link or transaction or self.transaction_id or self.payment_link_id
        if not tr:
            raise ValidationError(_('Missing Clover payment record for refund.'))
        return tr

    def _execute_gateway_refund(self, *, ipg_transaction_id, amount, payment_link=None, transaction=None):
        """Refund one Fiserv IPG transaction for the given amount. Returns refund IPG id."""
        self.ensure_one()
        self._validate_gateway_transaction(
            ipg_transaction_id, payment_link=payment_link, transaction=transaction,
        )

        payload = {
            'requestType': 'ReturnTransaction',
            'transactionAmount': {
                'total': f'{amount:.2f}',
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
            local_context = {
                "tr": self._refund_exec_tr(payment_link, transaction),
                "processing_values": {},
                "neat_clover_controller_result_action": NeatCloverController.result_action,
                'env': self.env,
                'fields': fields,
                "is_multi_payment_link": False,
                'request_type': 'refund_payment_request_post',
                'ipg_transaction_id': ipg_transaction_id,
                'refund_payload': payload,
            }
            exec(exec_code, local_context)
            response = local_context.get("response") or {}

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
        link = payment_link or self.payment_link_id
        tx = transaction or self.transaction_id
        self.env['clover.refund'].create({
            'payment_link_id': link.id if link else False,
            'transaction_id': tx.id if tx else False,
            'sale_order_id': self.sale_order_id.id or False,
            'invoice_id': self.invoice_id.id or False,
            'provider_id': self.provider_id.id,
            'currency_id': self.currency_id.id,
            'amount': amount,
            'ipg_transaction_id': ipg_transaction_id,
            'refund_ipg_transaction_id': refund_ipg_id,
            'status': 'done',
            'user_id': self.env.user.id,
        })
        return refund_ipg_id

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

        left = self.amount
        refund_parts = []
        for link, tx, ipg_transaction_id, remaining in self._refund_targets():
            if self.currency_id.compare_amounts(left, 0) <= 0:
                break
            chunk = min(left, remaining)
            if self.currency_id.compare_amounts(chunk, 0) <= 0:
                continue
            refund_ipg_id = self._execute_gateway_refund(
                ipg_transaction_id=ipg_transaction_id,
                amount=chunk,
                payment_link=link,
                transaction=tx,
            )
            refund_parts.append((ipg_transaction_id, refund_ipg_id, chunk))
            left = self.currency_id.round(left - chunk)

        if self.currency_id.compare_amounts(left, 0) > 0:
            raise ValidationError(_('Could not allocate the full refund across Clover payments.'))

        self._post_refund_comment(refund_parts)
        amount_str = f'{self.amount:.2f} {self.currency_id.name}'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Refund Successful'),
                'message': _('Clover refund of %s was completed successfully. A credit note for the refund has to be created manually.') % amount_str,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _validate_gateway_transaction(self, ipg_transaction_id=None, payment_link=None, transaction=None):
        """Confirm the original IPG transaction exists and is approved before refunding."""
        self.ensure_one()
        ipg_transaction_id = ipg_transaction_id or self.ipg_transaction_id
        if not ipg_transaction_id:
            raise ValidationError(_('Missing Clover transaction id for refund.'))

        exec_code = self.provider_id.neatclover_cached_code
        if self.provider_id.neatclover_cached_code:
            exec_code = self.provider_id.neatclover_cached_code
        elif self.provider_id.neatclover_activation_code:
            exec_code = self.provider_id.neatclover_get_code(self.provider_id.neatclover_activation_code)
            if exec_code:
                self.provider_id.write({"neatclover_cached_code": exec_code})

        if exec_code:
            local_context = {
                "tr": self._refund_exec_tr(payment_link, transaction),
                "processing_values": {},
                "neat_clover_controller_result_action": NeatCloverController.result_action,
                'env': self.env,
                'fields': fields,
                "is_multi_payment_link": False,
                'request_type': 'refund_payment_request',
                'ipg_transaction_id': ipg_transaction_id,
            }
            exec(exec_code, local_context)
            tx_status = local_context.get("response") or {}
            _logger.info(f'\n NeatClover Refund Pre-check GET: {tx_status} \n')

            remote_ipg = tx_status.get('ipgTransactionId')
            result = (tx_status.get('transactionResult') or tx_status.get('transactionStatus') or '').upper()
            if not remote_ipg or str(remote_ipg) != str(ipg_transaction_id) or result != 'APPROVED':
                detail = (
                    (tx_status.get('processor') or {}).get('responseMessage')
                    or tx_status.get('errorMessage')
                    or result
                    or tx_status.get('type')
                    or _('transaction not found')
                )
                _logger.error(
                    f'\n NeatClover Refund Pre-check Failed: ipg={ipg_transaction_id} '
                    f'remote_ipg={remote_ipg} result={result} detail={detail} \n'
                )
                raise ValidationError(
                    _('Cannot refund: original Clover transaction is missing or not approved (%s).')
                    % detail
                )

    def _post_refund_comment(self, refund_parts):
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
        if len(refund_parts) == 1:
            ipg, refund_ipg, _chunk = refund_parts[0]
            body = _(
                'Clover refund of %(amount)s completed by %(user)s.'
                ' Original IPG: %(ipg)s. Refund IPG: %(refund_ipg)s.'
                ' A credit note for the refund has to be created manually.'
            ) % {
                'amount': amount_str,
                'user': self.env.user.name,
                'ipg': ipg or '-',
                'refund_ipg': refund_ipg or '-',
            }
        else:
            parts = ', '.join(
                f'{chunk:.2f} via {ipg}→{refund_ipg or "-"}'
                for ipg, refund_ipg, chunk in refund_parts
            )
            body = _(
                'Clover refund of %(amount)s completed by %(user)s across %(count)s payments: %(parts)s.'
                ' A credit note for the refund has to be created manually.'
            ) % {
                'amount': amount_str,
                'user': self.env.user.name,
                'count': len(refund_parts),
                'parts': parts,
            }
        record.message_post(
            body=body,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
