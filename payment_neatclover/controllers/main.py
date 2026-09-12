import base64
import json
import hashlib
import hmac
import logging
import time
from urllib.parse import quote
from odoo.http import request
from odoo import _, http, fields
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)
from odoo.addons.payment_neatclover.const import RESPONSE_CODES_MAPPING


class NeatCloverController(http.Controller):

    result_action = "/neatclover/result"

    def _is_guid_reference(self, reference):
        if (reference or '').startswith('pl/'):
            return True

    def _handle_guid_link_invoices(self, reference, result_state):
        link_rec = request.env['clover.payment.link'].sudo().search([('reference', '=', reference)], limit=1)
        if not link_rec:
            return False

        target_status = 'paid' if result_state == 'done' else result_state

        if link_rec.status == 'paid' and result_state == 'error':
            return True
        if link_rec.status == target_status:
            return True
        if link_rec.status == 'paid' and target_status in ('pending', 'error'):
            return True

        if result_state in ('pending', 'error'):
            link_rec.sudo().write({'status': result_state})
            if link_rec.sale_order_ids and result_state == 'error':
                link_rec._cancel_sale_orders_payment_transaction()

        if link_rec.sale_order_ids:
            orders = link_rec.sale_order_ids.filtered(lambda o: o.state in ('draft', 'sent'))
            order_names = ', '.join(link_rec.sale_order_ids.mapped('name'))
            if result_state == 'done' and orders:
                link_rec._complete_sale_orders_payment_transaction()
                note_body = (
                    f"Payment was made for reference {reference}. "
                    f"{'Partial payment applied.' if link_rec.is_partial else 'Multiple sales orders were paid together.'} "
                    f"Sales orders in this payment link: {order_names}"
                )
                admin_user = request.env.ref('base.user_admin')
                for order in link_rec.sale_order_ids:
                    order.with_user(admin_user).sudo().message_post(
                        body=note_body,
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
                link_rec.sudo().write({'status': 'paid'})
            elif result_state == 'done':
                link_rec._complete_sale_orders_payment_transaction()
                link_rec.sudo().write({'status': 'paid'})
            return True

        invoices = link_rec.invoice_ids.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')
        invoice_names = ', '.join(link_rec.invoice_ids.mapped('name'))
        if result_state == 'done' and invoices:
            link_rec._complete_sale_orders_payment_transaction()

            note_body = (
                f"Payment was made for reference {reference}. "
                f"{'Partial payment applied.' if link_rec.is_partial else 'Multiple invoices were paid together.'} "
                f"Invoices in this payment link: {invoice_names}"
            )
            admin_user = request.env.ref('base.user_admin')
            for invoice in link_rec.invoice_ids:
                invoice.with_user(admin_user).sudo().message_post(
                    body=note_body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            link_rec.sudo().write({'status': 'paid'})
        elif result_state == 'done':
            link_rec.sudo().write({'status': 'paid'})
        return True

    def _confirm_sale_orders(self, orders):
        orders = orders.filtered(lambda o: o.state in ('draft', 'sent'))
        for order in orders:
            order.action_confirm()

    def _build_link_payload(self, link_rec):
        payload_data = {
            'link_id': link_rec.id,
            'reference': link_rec.reference,
            'provider_id': link_rec.provider_id.id,
        }
        payload_json = json.dumps(payload_data, separators=(',', ':')).encode()
        encoded_payload = base64.urlsafe_b64encode(payload_json).decode().rstrip('=')
        signature = hmac.new(self._get_link_signing_secret(), encoded_payload.encode(), hashlib.sha256).hexdigest()
        return f'{encoded_payload}.{signature}'

    def _neatclover_link_result_redirect(self, link_rec, status=None):
        """Return the customer to the page they paid from (Clover link or VT)."""
        status = status or {
            'paid': 'APPROVED',
            'pending': 'WAITING',
            'error': 'FAILED',
        }.get(link_rec.status, link_rec.status)

        vt_wizard = request.env['clover.vt.popup']
        if link_rec.neatclover_vt_wizard_id:
            vt_wizard = vt_wizard.sudo().browse(link_rec.neatclover_vt_wizard_id).exists()
        if not vt_wizard:
            vt_wizard = request.env['clover.vt.popup'].sudo().search([
                ('virtual_payment_id', '=', link_rec.id),
            ], limit=1)
        if link_rec.neatclover_vt_wizard_id or vt_wizard:
            redirect_url = (
                f"/neatclovervt/result?status={quote(status or '')}"
                f"&reference={quote(link_rec.reference or '', safe='')}"
            )
            _logger.info("clover_result response redirect=%s status=%s", redirect_url, status)
            return request.redirect(redirect_url)

        payload = self._build_link_payload(link_rec)
        redirect_url = f"/neatclover/payment_link/{payload}?status={status}"
        _logger.info("clover_result response redirect=%s status=%s", redirect_url, status)
        return request.redirect(redirect_url)

    @http.route(result_action, type='http', methods=['GET', 'POST'], auth='public', csrf=False, save_session=False)
    def clover_result(self, pt_clover=None, **payload):
        _logger.info("clover_result request pt_clover=%s payload=%s", pt_clover, payload)
        if pt_clover:
            if self._is_guid_reference(pt_clover):
                link_rec = request.env['clover.payment.link'].sudo().search([('reference', '=', pt_clover)], limit=1)
                if not link_rec:
                    raise ValidationError(
                        "NeatClover Multi Payment Link: " + _("No transaction found matching reference %s.", pt_clover)
                    )
                
                status = None
                if link_rec.provider_id.code == 'neatclover' and link_rec.status == 'draft':
                    tx = link_rec
                    exec_code = tx.provider_id.neatclover_cached_code
                    if tx.provider_id.neatclover_cached_code:
                        exec_code = tx.provider_id.neatclover_cached_code
                    elif tx.provider_id.neatclover_activation_code:
                        exec_code = tx.provider_id.neatclover_get_code(tx.provider_id.neatclover_activation_code)
                        if exec_code:
                            tx.provider_id.write({"neatclover_cached_code": exec_code})

                    if exec_code:
                        local_context = {"tr": tx, "processing_values": {}, 
                                        "neat_clover_controller_result_action": self.result_action,
                                        "is_multi_payment_link": False,
                                        'env': request.env, 'fields': fields, "request_type": 'take_payment_response', 'checkout_endpoint': tx.neatclover_checkout_id}
                        exec(exec_code, {}, local_context)
                        response = local_context.get("response") or {}
                        _logger.info("clover_result fiserv response for %s: %s", pt_clover, response)
                        status = response.get('transactionStatus')
                        link_rec.write({#'provider_reference': response.get('ipgTransactionDetails', {}).get('processor', {}).get('referenceNumber', False),
                                        'neatclover_ipg_transaction_id': response.get('ipgTransactionDetails', {}).get('ipgTransactionId', False)})

                        result_state = 'error'
                        if status in RESPONSE_CODES_MAPPING['done']:
                            result_state = 'done'
                        elif status in RESPONSE_CODES_MAPPING["pending"]:
                            result_state = 'pending'
                        elif status in RESPONSE_CODES_MAPPING["authorised"]:
                            result_state = 'pending'
                        elif status in RESPONSE_CODES_MAPPING["error"]:
                            result_state = 'error'
                        self._handle_guid_link_invoices(pt_clover, result_state)

                return self._neatclover_link_result_redirect(link_rec, status)

            tx = request.env["payment.transaction"].sudo().search([('neatclover_payment_identifier', '=', pt_clover)], limit=1)
            if not tx:
                raise ValidationError(
                    "NeatClover: " + _("No transaction found matching reference %s.", pt_clover)
                )
            elif tx and tx.provider_id.code == 'neatclover' and tx.state == 'draft':

                exec_code = tx.provider_id.neatclover_cached_code
                if tx.provider_id.neatclover_cached_code:
                    exec_code = tx.provider_id.neatclover_cached_code
                elif tx.provider_id.neatclover_activation_code:
                    exec_code = tx.provider_id.neatclover_get_code(tx.provider_id.neatclover_activation_code)
                    if exec_code:
                        tx.provider_id.write({"neatclover_cached_code": exec_code})

                if exec_code:
                    local_context = {"tr": tx, "processing_values": {}, 
                                    "neat_clover_controller_result_action": self.result_action,
                                    "is_multi_payment_link": False,
                                    'env': request.env, 'fields': fields, "request_type": 'take_payment_response', 'checkout_endpoint': tx.neatclover_checkout_id}
                    exec(exec_code, {}, local_context)
                    response = local_context.get("response") or {}
                    tx._process('neatclover', response)
            landing_route = '/payment/status'
            _logger.info("clover_result response redirect=%s", landing_route)
            return request.redirect(landing_route)
        _logger.info("clover_result response redirect=/payment/status")
        return request.redirect('/payment/status')

    def _get_link_signing_secret(self):
        return (request.env['ir.config_parameter'].sudo().get_param('database.secret') or '').encode()

    def _parse_link_payload(self, payload):
        try:
            encoded_payload, signature = payload.split('.', 1)
            _logger.info(f"\n---54 : {encoded_payload} : {signature}\n")
            expected_signature = hmac.new(self._get_link_signing_secret(), encoded_payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected_signature):
                return None
            padding = '=' * (-len(encoded_payload) % 4)
            data = json.loads(base64.urlsafe_b64decode((encoded_payload + padding).encode()).decode())
            return data
        except Exception:
            return None

    def _get_link_processing_values(self, link_rec, payload_provider_id=None, kwargs={}):
        link_rec = link_rec.sudo()
        return_status = (kwargs or {}).get('status')
        failed_return = return_status in ('FAILED', 'FRAUD', 'DECLINED', 'VALIDATION_FAILED')

        if link_rec.sale_order_ids:
            def _so_fully_paid(o):
                invoices = o.invoice_ids.filtered(lambda m: m.state == 'posted' and m.move_type == 'out_invoice')
                return bool(invoices) and all(
                    o.currency_id.compare_amounts(inv.amount_residual, 0) <= 0 for inv in invoices
                )
            if all(_so_fully_paid(o) for o in link_rec.sale_order_ids):
                return {'error': 'Sales orders are already fully paid.'}
        else:
            invoices = link_rec.invoice_ids.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')
            if not invoices:
                return {'error': 'No unpaid invoices available for payment.'}

        provider = None
        if payload_provider_id:
            _logger.info(f"\n Payload Provider ID {payload_provider_id} \n")
            provider = request.env['payment.provider'].sudo().browse(payload_provider_id).exists()
        if not provider:
            _logger.info(f"\n No Provider Found \n")
            provider = link_rec.provider_id.sudo()
        if not provider or provider.code != 'neatclover' or provider.state == 'disabled':
            return {'error': 'Clover provider is not configured.'}

        processing_values = link_rec.neatclover_get_processing_values(
            provider=provider,
            result_action=NeatCloverController.result_action,
            force_new=failed_return or link_rec.status == 'error',
        )
        if failed_return:
            processing_values['error'] = processing_values.get('error') or ''
        _logger.info("_get_link_processing_values response for %s: %s", link_rec.reference, processing_values)
        return processing_values

    def _wait_payment_link_final_status(self, link_rec, timeout_seconds=30):
        """Poll DB until paid/error/cancel or timeout. Returns status string from SQL."""
        if not link_rec:
            return None
        link_id = link_rec.id
        _logger.info(
            '_wait_payment_link_final_status waiting link_id=%s reference=%s',
            link_id, link_rec.reference,
        )
        db_status = None
        for attempt in range(timeout_seconds):
            request.env.cr.execute(
                'SELECT status FROM clover_payment_link WHERE id = %s',
                (link_id,),
            )
            row = request.env.cr.fetchone()
            db_status = row[0] if row else None
            _logger.info(
                '_wait_payment_link_final_status poll attempt=%s link_id=%s row=%s db_status=%s',
                attempt + 1, link_id, row, db_status,
            )
            # Commit so the next SELECT is outside a long idle txn and sees webhook commits.
            request.env.cr.commit()
            if db_status in ('paid', 'error', 'cancel'):
                return db_status
            time.sleep(1)
        _logger.info(
            '_wait_payment_link_final_status timeout link_id=%s link_status=%s',
            link_id, db_status,
        )
        return db_status

    @http.route('/neatclover/payment_link/<string:payload>', type='http', auth='public', website=True, csrf=False)
    def payment_link_page(self, payload, **kwargs):
        _logger.info("payment_link_page request payload=%s kwargs=%s", payload, kwargs)
        data = self._parse_link_payload(payload)
        if not data:
            _logger.info("payment_link_page response: not_found")
            return request.not_found()

        link_rec = request.env['clover.payment.link'].sudo().browse(data.get('link_id')).exists()
        if not link_rec or link_rec.reference != data.get('reference'):
            _logger.info("payment_link_page response: not_found")
            return request.not_found()

        status_param = kwargs.get('status') or ''
        waited_status = None
        _logger.info(
            "payment_link_page status_param=%s reference=%s link_status=%s",
            status_param, link_rec.reference, link_rec.status,
        )
        if status_param in ('WAITING', 'pending', 'PARTIAL'):
            _logger.info("payment_link_page WAITING start reference=%s", link_rec.reference)
            waited_status = self._wait_payment_link_final_status(link_rec)
            _logger.info(
                "payment_link_page WAITING done reference=%s waited_status=%s",
                link_rec.reference, waited_status,
            )

        sale_orders = link_rec.sale_order_ids
        order_pay_lines = []
        if sale_orders:
            currency_symbol = sale_orders[:1].currency_id.symbol if sale_orders else ''
            for order in sale_orders:
                g = getattr(order, 'get_portal_url', None)
                portal_url = g() if callable(g) else None
                order_pay_lines.append({
                    'name': order.name,
                    'amount': order.amount_total,
                    'portal_url': portal_url or ('/web#id=%s&model=sale.order&view_type=form' % order.id),
                })
            amount_total = sum(order['amount'] for order in order_pay_lines)
            def _so_fully_paid(o):
                invoices = o.invoice_ids.filtered(lambda m: m.state == 'posted' and m.move_type == 'out_invoice')
                return bool(invoices) and all(
                    o.currency_id.compare_amounts(inv.amount_residual, 0) <= 0 for inv in invoices
                )
            link_is_paid = bool(sale_orders) and all(_so_fully_paid(o) for o in sale_orders)
            invoices = request.env['account.move']  
        else:
            invoices = link_rec.invoice_ids.filtered(lambda m: m.state == 'posted')
            amount_total = sum(invoices.mapped('amount_total'))
            currency_symbol = invoices[:1].currency_id.symbol if invoices else ''
            link_is_paid = bool(invoices) and all(inv.payment_state == 'paid' for inv in invoices)
        if waited_status == 'paid':
            link_is_paid = True
        if link_is_paid and link_rec.status != 'paid':
            link_rec.sudo().write({'status': 'paid'})
        
        processing_values = self._get_link_processing_values(
            link_rec, payload_provider_id=data.get('provider_id'), kwargs=kwargs,
        ) if not link_is_paid else {}

        if waited_status == 'paid':
            display_status = 'APPROVED'
            display_link_status = 'paid'
        elif waited_status is not None:
            display_status = 'failure'
            display_link_status = 'error'
        else:
            display_status = kwargs.get('status')
            display_link_status = link_rec.status

        values = {
            'invoices': invoices,
            'sale_orders': sale_orders,
            'order_pay_lines': order_pay_lines,
            'amount_total': amount_total,
            'currency_symbol': currency_symbol,
            'payload': payload,
            'reference': link_rec.reference,
            'link_is_paid': link_is_paid,
            'status': display_status,
            'link_status': display_link_status,
            'payment_url': processing_values.get('payment_url'),
            'neatclover_use_iframe': processing_values.get('neatclover_use_iframe'),
            'payment_error': processing_values.get('error'),
        }
        _logger.info(
            "payment_link_page response reference=%s link_is_paid=%s link_status=%s status=%s payment_url=%s payment_error=%s",
            link_rec.reference, link_is_paid, display_link_status, display_status,
            processing_values.get('payment_url'), processing_values.get('error'),
        )
        return request.render('payment_neatclover.clover_payment_link_page', values)

    def _neatclover_schedule_failure_activity(self, record):
        """Schedule a failure activity on every linked sale order, at most once per record."""
        if not record or len(record) != 1:
            return False
        record = record.sudo()
        if record.neatclover_failure_activity_scheduled:
            return False

        provider = record.provider_id
        reference = record.reference
        target_records = record.sale_order_ids
        if not target_records and record._name == 'payment.transaction' and record.reference:
            target_records = request.env['sale.order'].sudo().search([
                ('name', '=', record.reference),
            ], limit=1)

        if not target_records:
            return False

        if not provider or not provider.neatclover_fallback_user_id:
            return False
        user_id = int(provider.neatclover_fallback_user_id)

        scheduled = 0
        for so in target_records:
            so.activity_schedule(
                act_type_xmlid='mail.mail_activity_data_todo',
                user_id=user_id,
                date_deadline=fields.Date.today(),
                summary="Payment Failed - Action Required",
                note=(
                    f"The payment failed after initial confirmation {reference}. "
                    "Please review and take action."
                ),
            )
            scheduled += 1

        if scheduled:
            record.write({'neatclover_failure_activity_scheduled': True})
            return True
        return False

    @http.route(
        "/neatclover/wh", type="http", auth="public", csrf=False, methods=["POST", "GET"]
    )
    def neatclover_wh(self, **kwargs):
        raw_body = request.httprequest.get_data(as_text=True)
        _logger.info("neatclover_wh request body=%s kwargs=%s", raw_body, kwargs)
        response = json.loads(raw_body)
        _logger.info("neatclover_wh parsed response=%s", response)
        order_id = response.get('orderId')
        checkout_id = response.get('checkoutId')
        status = response.get('transactionStatus')

        # WAITING (e.g. 3DS) has no approvedAmount; ack and wait for a final webhook.
        if status == 'WAITING':
            response_body = {
                'error': 'OK',
                'message': 'Ignored WAITING',
                'reference': order_id,
                'transaction_status': status,
            }
            _logger.info("neatclover_wh response: %s", response_body)
            return request.make_json_response(response_body, status=200)

        result_state = 'error'
        if status in RESPONSE_CODES_MAPPING['done']:
            result_state = 'done'
        elif status in RESPONSE_CODES_MAPPING["pending"]:
            result_state = 'pending'
        elif status in RESPONSE_CODES_MAPPING["authorised"]:
            result_state = 'pending'
        elif status in RESPONSE_CODES_MAPPING["error"]:
            result_state = 'error'
        incoming_failed = result_state == 'error'

        if order_id and checkout_id:
            link_rec = request.env['clover.payment.link'].sudo().search([
                ('neatclover_checkout_id', '=', checkout_id),
                '|',
                ('reference', '=', order_id),
                ('sale_order_ids.name', '=', order_id),
            ], limit=1)
            if link_rec and link_rec.provider_id.code == 'neatclover':
                # Late failure only: previously paid, then a later error webhook.
                if link_rec.status == 'paid' and incoming_failed:
                    self._neatclover_schedule_failure_activity(link_rec)
                    response_body = {
                        'error': 'OK',
                        'message': 'OK',
                        'reference': link_rec.reference,
                        'transaction_status': status,
                    }
                    _logger.info("neatclover_wh response: %s", response_body)
                    return request.make_json_response(response_body, status=200)
                if link_rec.status == 'error' and incoming_failed:
                    response_body = {
                        'error': 'OK',
                        'message': 'Already processed',
                        'reference': link_rec.reference,
                        'transaction_status': status,
                    }
                    _logger.info("neatclover_wh response: %s", response_body)
                    return request.make_json_response(response_body, status=200)
                if link_rec.status not in ['paid', 'error']:
                    link_rec.write({
                        'neatclover_ipg_transaction_id': response.get('ipgTransactionDetails', {}).get('ipgTransactionId', False),
                    })
                    self._handle_guid_link_invoices(link_rec.reference, result_state)
                    response_body = {
                        'error': 'OK',
                        'message': 'OK',
                        'result_state': result_state,
                        'transaction_status': status,
                    }
                    _logger.info("neatclover_wh response: %s", response_body)
                    return request.make_json_response(response_body, status=200)

            tx = request.env['payment.transaction'].sudo().search([
                ('reference', '=', order_id),
                ('neatclover_checkout_id', '=', checkout_id),
            ], limit=1)
            if tx and tx.provider_id.code == 'neatclover':
                # Late failure only: previously done, then a later error webhook.
                if tx.state == 'done' and incoming_failed:
                    self._neatclover_schedule_failure_activity(tx)
                    response_body = {
                        'error': 'OK',
                        'message': 'OK',
                        'reference': tx.reference,
                        'transaction_status': status,
                    }
                    _logger.info("neatclover_wh response: %s", response_body)
                    return request.make_json_response(response_body, status=200)
                if tx.state == 'error' and incoming_failed:
                    response_body = {
                        'error': 'OK',
                        'message': 'Already processed',
                        'reference': tx.reference,
                        'transaction_status': status,
                    }
                    _logger.info("neatclover_wh response: %s", response_body)
                    return request.make_json_response(response_body, status=200)
                if tx.state not in ['done', 'error']:
                    tx._process('neatclover', response)
                    response_body = {
                        'error': 'OK',
                        'message': 'OK',
                        'reference': order_id,
                        'transaction_status': status,
                    }
                    _logger.info("neatclover_wh response: %s", response_body)
                    return request.make_json_response(response_body, status=200)

        link_rec = request.env['clover.payment.link'].sudo().search([
            '|',
            ('reference', '=', order_id),
            ('sale_order_ids.name', '=', order_id),
        ], limit=1)
        if not link_rec:
            link_rec = request.env['payment.transaction'].sudo().search([
                ('reference', '=', order_id),
            ], limit=1)

        if link_rec and link_rec._name == 'clover.payment.link' and link_rec.provider_id.code == 'neatclover':
            if link_rec.status == 'paid' and incoming_failed:
                self._neatclover_schedule_failure_activity(link_rec)
                response_body = {
                    'error': 'OK',
                    'message': 'OK',
                    'reference': link_rec.reference,
                    'transaction_status': status,
                }
                _logger.info("neatclover_wh response: %s", response_body)
                return request.make_json_response(response_body, status=200)
            if link_rec.status not in ['paid', 'error'] and incoming_failed:
                link_rec.write({
                    'neatclover_ipg_transaction_id': response.get('ipgTransactionDetails', {}).get('ipgTransactionId', False),
                })
                self._handle_guid_link_invoices(link_rec.reference, result_state)
                response_body = {
                    'error': 'OK',
                    'message': 'OK',
                    'reference': link_rec.reference,
                    'result_state': result_state,
                    'transaction_status': status,
                }
                _logger.info("neatclover_wh response: %s", response_body)
                return request.make_json_response(response_body, status=200)

        if link_rec:
            is_paid = (
                link_rec.status == 'paid'
                if link_rec._name == 'clover.payment.link'
                else link_rec.state == 'done'
            )
            if is_paid and incoming_failed:
                self._neatclover_schedule_failure_activity(link_rec)
                response_body = {
                    'error': 'OK',
                    'message': 'OK',
                    'reference': link_rec.reference,
                    'transaction_status': status,
                }
                _logger.info("neatclover_wh response: %s", response_body)
                return request.make_json_response(response_body, status=200)
            is_failed = (
                link_rec.status == 'error'
                if link_rec._name == 'clover.payment.link'
                else link_rec.state == 'error'
            )
            if is_failed and incoming_failed:
                response_body = {
                    'error': 'OK',
                    'message': 'Already processed',
                    'reference': link_rec.reference,
                    'transaction_status': status,
                }
                _logger.info("neatclover_wh response: %s", response_body)
                return request.make_json_response(response_body, status=200)
            response_body = {
                'error': 'OK',
                'message': 'OK',
                'reference': link_rec.reference,
                'transaction_status': status,
            }
            _logger.info("neatclover_wh response: %s", response_body)
            return request.make_json_response(response_body, status=200)

        response_body = {'error': 'Bad Request', 'message': 'Bad Request', 'order_id': order_id}
        _logger.info("neatclover_wh response: %s", response_body)
        return request.make_json_response(response_body, status=400)

    @http.route('/neatclovervt/invoice_payment/<int:wizard_id>', type='http', auth='user', website=True)
    def neatclovervt_invoice_payment_page(self, wizard_id, **kwargs):
        _logger.info("neatclovervt_invoice_payment_page request wizard_id=%s kwargs=%s", wizard_id, kwargs)
        wizard = request.env['clover.vt.popup'].sudo().browse(wizard_id).exists()
        if not wizard:
            _logger.info("neatclovervt_invoice_payment_page response: not_found")
            return request.not_found()
        vt_providers = request.env['payment.provider'].sudo().search([
            ('code', '=', 'neatclover'),
            ('state', '!=', 'disabled'),
        ])
        _logger.info(
            "neatclovervt_invoice_payment_page response wizard_id=%s reference=%s provider_count=%s",
            wizard_id, wizard.reference, len(vt_providers),
        )
        return request.render('payment_neatclover.clover_vt_invoice_payment_page', {
            'wizard': wizard,
            'vt_providers': vt_providers,
        })

    def _neatclovervt_invoice_provider_for_wizard(self, wizard, provider_id):
        provider = request.env['payment.provider'].sudo().browse(int(provider_id)).exists()
        if not provider or provider.code != 'neatclover' or provider.state == 'disabled':
            return request.env['payment.provider']
        if not wizard.virtual_payment_id:
            return request.env['payment.provider']
        return provider

    @http.route(
        '/neatclovervt/invoice_payment/<int:wizard_id>/checkout',
        type='http',
        auth='user',
        website=True,
        methods=['POST'],
        csrf=False,
    )
    def neatclovervt_invoice_payment_checkout(self, wizard_id, **kwargs):
        _logger.info("neatclovervt_invoice_payment_checkout request wizard_id=%s", wizard_id)
        wizard = request.env['clover.vt.popup'].sudo().browse(wizard_id).exists()
        if not wizard:
            response_body = {'ok': False, 'error': 'not_found'}
            _logger.info("neatclovervt_invoice_payment_checkout response: %s", response_body)
            return request.make_json_response(response_body, status=404)
        payload = request.get_json_data() or {}
        _logger.info("neatclovervt_invoice_payment_checkout payload=%s", payload)
        provider_id = payload.get('provider_id')
        transaction_origin = (payload.get('transaction_origin') or payload.get('origin') or 'PHONE')
        if isinstance(transaction_origin, str):
            transaction_origin = transaction_origin.upper().strip()
        else:
            transaction_origin = 'PHONE'
        if transaction_origin not in ('MAIL', 'PHONE'):
            response_body = {'ok': False, 'error': 'invalid_origin'}
            _logger.info("neatclovervt_invoice_payment_checkout response: %s", response_body)
            return request.make_json_response(response_body, status=400)
        if not provider_id:
            response_body = {'ok': False, 'error': 'missing_provider'}
            _logger.info("neatclovervt_invoice_payment_checkout response: %s", response_body)
            return request.make_json_response(response_body, status=400)
        provider = self._neatclovervt_invoice_provider_for_wizard(wizard, provider_id)
        if not provider:
            response_body = {'ok': False, 'error': 'invalid_provider'}
            _logger.info("neatclovervt_invoice_payment_checkout response: %s", response_body)
            return request.make_json_response(response_body, status=400)

        link_rec = wizard.virtual_payment_id.sudo()
        link_rec.write({'provider_id': provider.id})

        if link_rec.is_partial:
            try:
                amount = float(payload.get('amount'))
            except (TypeError, ValueError):
                response_body = {'ok': False, 'error': 'invalid_amount'}
                _logger.info("neatclovervt_invoice_payment_checkout response: %s", response_body)
                return request.make_json_response(response_body, status=400)
            try:
                prepared_amount = link_rec._prepare_partial_amount(amount)
            except ValidationError as exc:
                response_body = {'ok': False, 'error': 'invalid_amount', 'message': exc.args[0] if exc.args else str(exc)}
                _logger.info("neatclovervt_invoice_payment_checkout response: %s", response_body)
                return request.make_json_response(response_body, status=400)
            if link_rec.sale_order_ids:
                tx = link_rec._get_sale_orders_payment_transaction()
                if tx and tx.state == 'draft':
                    tx.write({'amount': prepared_amount})
                else:
                    link_rec._create_sale_orders_payment_transaction()

        reuse_existing = (
            wizard.checkout_id
            and wizard.transaction_key
            and wizard.payment_url
            and wizard.provider_id == provider
            and wizard.transaction_origin == transaction_origin
            and not link_rec.is_partial
        )
        if reuse_existing:
            processing_values = {
                'transaction_reference': wizard.transaction_reference,
                'transaction_key': wizard.transaction_key,
                'checkout_id': wizard.checkout_id,
                'payment_url': wizard.payment_url,
            }
        else:
            processing_values = link_rec.neatclover_get_processing_values(
                provider=provider,
                result_action=self.result_action,
                transaction_origin=transaction_origin,
            )
            if not processing_values.get('checkout_id') or not processing_values.get('transaction_key') or not processing_values.get('payment_url'):
                response_body = {'ok': False, 'error': 'checkout_failed'}
                _logger.info("neatclovervt_invoice_payment_checkout response: %s processing_values=%s", response_body, processing_values)
                return request.make_json_response(response_body, status=400)

            wizard.sudo().write({
                'provider_id': provider.id,
                'transaction_origin': transaction_origin,
                'transaction_reference': processing_values.get('transaction_reference') or wizard.virtual_payment_id.reference,
                'transaction_key': processing_values.get('transaction_key'),
                'checkout_id': processing_values.get('checkout_id'),
                'payment_url': processing_values.get('payment_url'),
                'clover_url': provider.neatclover_get_base_url() or '',
            })
        response_body = {
            'ok': True,
            'transaction_reference': wizard.transaction_reference,
            'transaction_key': wizard.transaction_key,
            'checkout_id': wizard.checkout_id,
            'payment_url': wizard.payment_url,
            'clover_url': wizard.clover_url,
            'transaction_origin': wizard.transaction_origin,
            'provider_id': provider.id,
        }
        _logger.info("neatclovervt_invoice_payment_checkout response: %s", response_body)
        return request.make_json_response(response_body)

    @http.route('/neatclovervt/invoice_payment/<int:wizard_id>/pay', type='http', auth='user', website=True)
    def neatclovervt_invoice_payment_pay_page(self, wizard_id, **kwargs):
        _logger.info("neatclovervt_invoice_payment_pay_page request wizard_id=%s kwargs=%s", wizard_id, kwargs)
        wizard = request.env['clover.vt.popup'].sudo().browse(wizard_id).exists()
        if not wizard:
            _logger.info("neatclovervt_invoice_payment_pay_page response: not_found")
            return request.not_found()
        provider = wizard.provider_id
        if provider and wizard.virtual_payment_id and not wizard.checkout_id:
            processing_values = wizard.virtual_payment_id.neatclover_get_processing_values(
                provider=provider,
                result_action=self.result_action,
                transaction_origin=wizard.transaction_origin or 'PHONE',
            )
            if processing_values.get('checkout_id') and processing_values.get('transaction_key') and processing_values.get('payment_url'):
                wizard.sudo().write({
                    'transaction_reference': processing_values.get('transaction_reference') or wizard.transaction_reference,
                    'transaction_key': processing_values.get('transaction_key'),
                    'checkout_id': processing_values.get('checkout_id'),
                    'payment_url': processing_values.get('payment_url'),
                })
        _logger.info(
            "neatclovervt_invoice_payment_pay_page response wizard_id=%s reference=%s checkout_id=%s payment_url=%s provider_id=%s",
            wizard_id, wizard.transaction_reference, wizard.checkout_id, wizard.payment_url, provider.id if provider else None,
        )
        return request.render('payment_neatclover.clover_vt_invoice_payment_checkout', {
            'transaction_reference': wizard.transaction_reference,
            'transaction_key': wizard.transaction_key,
            'checkout_id': wizard.checkout_id,
            'payment_url': wizard.payment_url,
            'clover_url': wizard.clover_url,
            'provider_id': provider.id if provider else '',
            'wizard_id': wizard.id,
        })

    @http.route('/neatclovervt/payment-status', type='http', auth='user', methods=['GET'], csrf=False)
    def neatclovervt_payment_status(self, reference=None, **kwargs):
        if not reference:
            return request.make_json_response({'ok': False, 'error': 'missing_reference'}, status=400)
        link = request.env['clover.payment.link'].sudo().search([('reference', '=', reference)], limit=1)
        if not link:
            return request.make_json_response({'ok': False, 'error': 'not_found'}, status=404)
        return request.make_json_response({
            'ok': True,
            'status': link.status,
            'reference': link.reference,
        })

    @http.route('/neatclovervt/result', type='http', auth='public', website=True, csrf=False)
    def neatclovervt_result_close(self, status=None, reference=None, **kwargs):
        status = status or ''
        # WAITING: poll backend until paid/error (webhook may finish after redirect).
        if status in ('WAITING', 'pending', 'PARTIAL'):
            link = request.env['clover.payment.link']
            if reference:
                link = link.sudo().search([('reference', '=', reference)], limit=1)
            waited_status = self._wait_payment_link_final_status(link) if link else None
            status = 'APPROVED' if waited_status == 'paid' else 'FAILED'

        success = status in ('APPROVED', 'done', 'paid')
        declined = status in ('FAILED', 'FRAUD', 'DECLINED', 'error', 'VALIDATION_FAILED')
        _logger.info(
            "neatclovervt_result_close status=%s success=%s reference=%s",
            status, success, reference,
        )
        return request.render('payment_neatclover.clover_vt_result_close', {
            'status': status,
            'success': success,
            'declined': declined,
        })
