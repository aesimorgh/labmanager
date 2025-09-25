# core/utils/normalizers.py
# Helper to normalize Jalali date strings like "۱۴۰۴/۰۷/۰۴" -> "1404-07-04"
# Converts Persian/Arabic digits to ASCII and replaces "/" with "-"

DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

def normalize_jalali_date_str(s: str | None) -> str | None:
    if not s:
        return s
    s = s.strip().translate(DIGIT_MAP)
    s = s.replace("/", "-")
    return s
