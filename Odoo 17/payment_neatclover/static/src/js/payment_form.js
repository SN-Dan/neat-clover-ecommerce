/** @odoo-module */

import { _t } from '@web/core/l10n/translation';
import paymentForm from '@payment/js/payment_form';

paymentForm.include({

     /**
     * Open the inline form of the selected payment option, if any.
     *
     * @override method from @payment/js/payment_form
     * @private
     * @param {Event} ev
     * @return {void}
     */
    async _selectPaymentOption(ev) {
        await this._super(...arguments);
    },
        /**
     * Prepare the inline form of Stripe for direct payment.
     *
     * @override method from @payment/js/payment_form
     * @private
     * @param {number} providerId - The id of the selected payment option's provider.
     * @param {string} providerCode - The code of the selected payment option's provider.
     * @param {number} paymentOptionId - The id of the selected payment option
     * @param {string} paymentMethodCode - The code of the selected payment method, if any.
     * @param {string} flow - The online payment flow of the selected payment option.
     * @return {void}
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'neatclover') {
            this._super(...arguments);
            return;
        }
        
        if (flow === 'token') {
            return;
        }

        this._setPaymentFlow('direct');
    },

    // #=== PAYMENT FLOW ===#

    /**
     * feedback from a payment provider and redirect the customer to the status page.
     *
     * @override method from payment.payment_form
     * @private
     * @param {string} providerCode - The code of the selected payment option's provider.
     * @param {number} paymentOptionId - The id of the selected payment option.
     * @param {string} paymentMethodCode - The code of the selected payment method, if any.
     * @param {object} processingValues - The processing values of the transaction.
     * @return {void}
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'neatclover') {
            this._super(...arguments);
            return;
        }

        console.debug('neatclover: processingValues', processingValues);
        const checkoutUrl = processingValues.payment_url || processingValues.checkout_url;
        const useIframe = [true, 'true', 1, '1', 'True', 'TRUE'].includes(processingValues.neatclover_use_iframe);
        console.debug('neatclover: checkoutUrl', checkoutUrl, 'useIframe', useIframe);
        if (!checkoutUrl) {
            alert(_t("Clover did not return a checkout URL. Please contact support."));
            return;
        }

        this.call('ui', 'unblock');
        const popup = document.querySelector('#clover_popup') || createCloverIframePopup();
        const iframe = document.querySelector('#clover_checkout_iframe');
        console.debug('neatclover: popup/iframe', popup, iframe, 'useIframe', useIframe);
        if (useIframe && popup && iframe) {
            document.querySelectorAll('.modal').forEach(m => {
                if (m.id !== 'clover_popup') {
                    m.inert = true;
                }
            });
            iframe.src = checkoutUrl;
            popup.style.display = 'block';
            popup.focus();
            return;
        }
        console.warn('neatclover: iframe not enabled; redirecting to checkout.');
        window.top.location.href = checkoutUrl;
    },
    /**
     * Redirect the customer to the status route.
     *
     * @override method from payment.payment_form
     * @private
     * @param {string} providerCode - The code of the selected payment option's provider.
     * @param {number} paymentOptionId - The id of the selected payment option.
     * @param {string} paymentMethodCode - The code of the selected payment method, if any.
     * @param {object} processingValues - The processing values of the transaction.
     * @return {void}
     */
    async _processTokenFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'neatclover') {
            this._super(...arguments);
            return;
        }

        console.debug('neatclover: processingValues', processingValues);
        const checkoutUrl = processingValues.payment_url || processingValues.checkout_url;
        const useIframe = [true, 'true', 1, '1', 'True', 'TRUE'].includes(processingValues.neatclover_use_iframe);
        console.debug('neatclover: checkoutUrl', checkoutUrl, 'useIframe', useIframe);
        if (!checkoutUrl) {
            alert(_t("Clover did not return a checkout URL. Please contact support."));
            return;
        }

        this.call('ui', 'unblock');
        const popup = document.querySelector('#clover_popup') || createCloverIframePopup();
        const iframe = document.querySelector('#clover_checkout_iframe');
        console.debug('neatclover: popup/iframe', popup, iframe, 'useIframe', useIframe);
        if (useIframe && popup && iframe) {
            document.querySelectorAll('.modal').forEach(m => {
                if (m.id !== 'clover_popup') {
                    m.inert = true;
                }
            });
            iframe.src = checkoutUrl;
            popup.style.display = 'block';
            popup.focus();
            return;
        }
        console.warn('neatclover: iframe is not available or disabled. Redirecting to checkout page.');
        window.top.location.href = checkoutUrl;
    },

});

// Handle closing the Clover popup and restoring focus/inert state
function closeCloverPopup() {
    const popup = document.getElementById('clover_popup');
    const iframe = document.getElementById('clover_checkout_iframe');
    
    if (popup) {
        popup.style.display = 'none';
        // Restore other modals
        document.querySelectorAll('.modal').forEach(m => {
            m.inert = false;
        });
    }
    
    if (iframe) {
        iframe.src = '';
    }
}

// Allow closing via close button (if needed for external use)
if (typeof window !== 'undefined') {
    window.closeCloverPopup = closeCloverPopup;
}

function createCloverIframePopup() {
    if (document.querySelector('#clover_popup')) {
        return document.querySelector('#clover_popup');
    }

    const popup = document.createElement('div');
    popup.id = 'clover_popup';
    popup.setAttribute('role', 'dialog');
    popup.setAttribute('aria-modal', 'true');
    popup.setAttribute('aria-label', 'Clover Secure Payment');
    popup.style.cssText = 'display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 9999;';

    const inner = document.createElement('div');
    inner.style.cssText = 'background: #fff; padding: 0; width: 100%; max-width: 820px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); border-radius: 12px; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); overflow: hidden;';

    const header = document.createElement('div');
    header.style.cssText = 'background: linear-gradient(135deg, #1f7a4a 0%, #28a745 100%); padding: 16px 24px; display: flex; align-items: center; justify-content: space-between;';
    const title = document.createElement('span');
    title.style.cssText = 'color: #fff; font-size: 1rem; font-weight: 600;';
    title.textContent = '🔒 Secure Payment — Clover';
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.id = 'clover_popup_close';
    closeButton.style.cssText = 'background: none; border: none; color: #fff; font-size: 1.2rem; cursor: pointer; line-height: 1;';
    closeButton.innerHTML = '✕';
    closeButton.addEventListener('click', closeCloverPopup);
    header.appendChild(title);
    header.appendChild(closeButton);

    const iframe = document.createElement('iframe');
    iframe.id = 'clover_checkout_iframe';
    iframe.src = '';
    iframe.width = '100%';
    iframe.height = '650';
    iframe.frameBorder = '0';
    iframe.scrolling = 'auto';
    iframe.allow = 'payment';
    iframe.setAttribute('allowpaymentrequest', 'true');
    iframe.style.cssText = 'display: block; border: none; width: 100%; height: 650px;';

    inner.appendChild(header);
    inner.appendChild(iframe);
    popup.appendChild(inner);
    document.body.appendChild(popup);
    return popup;
}