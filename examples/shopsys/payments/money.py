"""Money formatting shared by the billing side of the system."""


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100}.{abs(cents) % 100:02d}"
