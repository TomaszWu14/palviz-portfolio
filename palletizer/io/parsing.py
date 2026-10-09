import math as _math


def parse_float(value) -> float:
    s = str(value).strip().replace(",", ".")
    if s.lower() in ("", "nan", "none", "null", "-"):
        raise ValueError(f"Pusta lub nieprawidłowa wartość liczbowa: {value!r}")
    result = float(s)
    if _math.isnan(result) or _math.isinf(result):
        raise ValueError(f"Nieprawidłowa wartość liczbowa: {value!r}")
    return result


def parse_int(value) -> int:
    s = str(value).strip().replace(",", ".")   # tolerate European decimal comma ("12,0")
    if s.lower() in ("", "nan", "none", "null", "-"):
        raise ValueError(f"Pusta lub nieprawidłowa wartość całkowita: {value!r}")
    f = float(s)
    if _math.isnan(f) or _math.isinf(f) or f != int(f):
        # reject non-integral ("12.7") instead of silently truncating to 12
        raise ValueError(f"Wartość nie jest liczbą całkowitą: {value!r}")
    return int(f)  # toleruje "12.0"
