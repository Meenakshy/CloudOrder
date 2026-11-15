from enum import StrEnum


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class InvalidOrderTransition(ValueError):
    pass


_ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING: {
        OrderStatus.PROCESSING,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PROCESSING: {
        OrderStatus.COMPLETED,
        OrderStatus.REJECTED,
        OrderStatus.FAILED,
    },
}


def allowed_transition(current: str, target: str) -> bool:
    try:
        return OrderStatus(target) in _ALLOWED_TRANSITIONS.get(OrderStatus(current), set())
    except ValueError:
        return False