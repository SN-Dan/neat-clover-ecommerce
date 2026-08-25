# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.
# This module extends Odoo's payment framework.
# Odoo is a trademark of Odoo S.A.

DEFAULT_PAYMENT_METHODS_CODES = [
    'card'
]

RESPONSE_CODES_MAPPING = {
    # 'draft': ('INITIATED',),
    'pending': ('WAITING', 'PARTIAL'),
    'authorised': ('INITIATED',),
    'done': ('APPROVED',),
    'error': ('FAILED', 'FRAUD', 'VALIDATION_FAILED', 'DECLINED'),
}

LICENSE_VERSION = 'clover-v1'