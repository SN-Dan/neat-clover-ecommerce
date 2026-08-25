# -*- coding: utf-8 -*-
from odoo import _, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_open_neatclover_link_popup(self):
        view = self.env.ref('payment_neatclover.clover_link_popup_view_form')
        return {
            'name': _('Pay by Clover Link'),
            'type': 'ir.actions.act_window',
            'res_model': 'clover.link.popup',
            'view_mode': 'form',
            'view_id': view.id,
            'target': 'new',
            'context': {
                **self.env.context,
                'active_model': 'sale.order',
                'active_ids': self.ids,
            },
        }

    def action_open_clover_vt_popup(self):
        wizard = self.env['clover.vt.popup'].create_from_orders(self)
        view = self.env.ref('payment_neatclover.clover_vt_popup_view_form')
        return {
            'name': _('Pay by Clover Virtual Terminal'),
            'type': 'ir.actions.act_window',
            'res_model': 'clover.vt.popup',
            'view_mode': 'form',
            'view_id': view.id,
            'res_id': wizard.id,
            'target': 'new',
            'context': {
                **self.env.context,
                'dialog_size': 'small',
            },
        }

    def action_open_neatclover_refund_popup(self):
        view = self.env.ref('payment_neatclover.clover_refund_popup_view_form')
        return {
            'name': _('Clover Refund'),
            'type': 'ir.actions.act_window',
            'res_model': 'clover.refund.popup',
            'view_mode': 'form',
            'view_id': view.id,
            'target': 'new',
            'context': {
                **self.env.context,
                'active_model': 'sale.order',
                'active_ids': self.ids,
            },
        }
