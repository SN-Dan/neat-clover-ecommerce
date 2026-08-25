/** @odoo-module */

import { _t } from '@web/core/l10n/translation';
import paymentForm from '@payment/js/payment_form';

paymentForm.include({

    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'neatclover') {
            this._super(...arguments);
            return;
        }
        if (flow === 'token') return;
        this._setPaymentFlow('direct');
    },

    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'neatclover') {
            this._super(...arguments);
            return;
        }
        const checkoutUrl = processingValues.payment_url || processingValues.checkout_url;
        if (!checkoutUrl) {
            alert(_t('Clover did not return a checkout URL. Please contact support.'));
            return;
        }
        this.call('ui', 'unblock');
        const popup = document.querySelector('#clover_popup');
        const iframe = document.querySelector('#clover_checkout_iframe');
        if (popup && iframe) {
            iframe.src = checkoutUrl;
            popup.style.display = 'block';
        }
    },

});

window.addEventListener('message', function (e) {
    try {
        var data = e.data;
        if (typeof data === 'string') {
            try { data = JSON.parse(data); } catch (err) { /* ignore */ }
        }
        if (!data || data.type !== 'clover_result') return;
        var target = data.target || '/payment/status';
        var popup = document.getElementById('clover_popup');
        if (popup) {
            popup.style.display = 'none';
            var iframe = document.getElementById('clover_checkout_iframe');
            if (iframe) { iframe.src = ''; }
        }
        try {
            if (window.top && window.top !== window.self) {
                window.top.location.href = target;
            } else {
                window.location.href = target;
            }
        } catch (err) {
            window.location.href = target;
        }
    } catch (err) { /* ignore */ }
}, false);
