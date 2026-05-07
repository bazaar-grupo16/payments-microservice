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
