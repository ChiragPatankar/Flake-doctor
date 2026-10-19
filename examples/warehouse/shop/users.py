import re

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def valid_email(address):
    return bool(EMAIL.match(address or ""))


def display_name(first, last):
    first, last = (first or "").strip(), (last or "").strip()
    return ("%s %s" % (first, last)).strip() or "Customer"
