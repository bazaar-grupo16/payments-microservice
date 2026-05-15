"""Domain exception types for the payments microservice."""


class PaymentError(Exception):
    """Base exception for payment-related errors."""


class MercadoPagoError(PaymentError):
    """Raised when the MercadoPago API returns an error."""

    def __init__(self, detail: str = "MercadoPago service unavailable"):
        self.detail = detail
        super().__init__(self.detail)


class PaymentNotFoundError(PaymentError):
    """Raised when a payment record is not found."""

    def __init__(self, detail: str = "Payment not found"):
        self.detail = detail
        super().__init__(self.detail)


class PaymentItemNotFoundError(PaymentError):
    """Raised when a specific item is not found within a payment."""

    def __init__(self, detail: str = "Payment item not found"):
        self.detail = detail
        super().__init__(self.detail)


class PaymentItemAlreadyRefundedError(PaymentError):
    """Raised when a payment item has already been refunded."""

    def __init__(self, detail: str = "Payment item has already been refunded"):
        self.detail = detail
        super().__init__(self.detail)


class PaymentNotRefundableError(PaymentError):
    """Raised when a payment is not in a state that allows refunds."""

    def __init__(self, detail: str = "Payment is not in a refundable state"):
        self.detail = detail
        super().__init__(self.detail)
