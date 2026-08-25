{
    "name": "Payment Provider: Clover",
    "version": "19.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "sequence": 350,
    "summary": "Clover E-Commerce/Online Payments Official Integration — checkout, payment links, virtual terminal & refunds.",
    "description": """
        Clover online payments integration for Odoo.
        Includes e-commerce checkout, payment links, virtual terminal, and refunds.

        This module extends Odoo's payment framework.
        Odoo is a trademark of Odoo S.A.

        LICENSE: This module is licensed under LGPL-3.
        See LICENSE file for complete terms.
    """,
    "author": "SNS Software",
    "maintainer": "SNS Software",
    "website": "https://www.sns-software.com",
    "depends": ["payment", "sale"],
    "images": ["static/description/main.gif"],
    "data": [
        "security/neatclover_security.xml",
        "security/ir.model.access.csv",
        "views/payment_provider_views.xml",
        "views/payment_neatclover_templates.xml",
        "data/payment_provider_data.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "wizard/clover_link_popup_views.xml",
        "wizard/clover_refund_popup_views.xml",
        "views/clover_payment_link_templates.xml",
        "wizard/clover_vt_popup_view.xml",
        "views/clover_vt_payment_templates.xml"
    ],
    "assets": {
        "web.assets_backend": [
            "payment_neatclover/static/src/js/neatclover.js",
            "payment_neatclover/static/src/css/neatclover.css",
        ],
        "web.assets_frontend": [
            "payment_neatclover/static/src/js/payment_form.js",
            "payment_neatclover/static/src/js/neatclover.js",
            "payment_neatclover/static/src/css/neatclover.css",
        ],
    },
    "installable": True,
    "license": "LGPL-3",
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
}

