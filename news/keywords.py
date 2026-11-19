"""
Keyword dictionaries for Persian financial news classification.
"""

# High-impact negative: block trades if detected
NEGATIVE_HIGH = [
    "تحریم", "تحریم جدید", "بسته شدن", "توقف تولید", "توقف نماد",
    "زیان سنگین", "زیان انباشته", "تعدیل منفی", "افت شدید",
    "لغو قرارداد", "فسخ قرارداد", "جریمه", "دعوی حقوقی",
    "تعطیلی", "اخراج", "استعفای مدیرعامل", "بحران",
]

# Medium-impact negative: adds warning
NEGATIVE_MEDIUM = [
    "کاهش", "افت", "نزول", "ریزش", "ضعف", "بدهی", "استقراض",
    "افزایش هزینه", "کاهش نرخ", "کاهش فروش", "کاهش صادرات",
    "محدودیت", "تعلیق", "ممنوعیت",
]

# High-impact positive
POSITIVE_HIGH = [
    "قرارداد بزرگ", "افزایش سرمایه", "تعدیل مثبت", "رشد چشمگیر",
    "رکورد تولید", "رکورد فروش", "افتتاح طرح", "بهره‌برداری",
    "صادرات جدید", "توافق", "رفع تحریم", "افزایش سرمایه از تجدید",
]

# Medium-impact positive
POSITIVE_MEDIUM = [
    "رشد", "افزایش", "سود", "توسعه", "توسعه تولید", "بهبود",
    "افزایش فروش", "افزایش صادرات", "افزایش نرخ", "موفقیت",
]

# Category hints
CATEGORY_HINTS = {
    "company": ["شرکت", "هیئت‌مدیره", "مجمع", "سهامدار", "مدیرعامل"],
    "macro": ["تورم", "نقدینگی", "نرخ ارز", "دلار", "بودجه", "بانک مرکزی", "بهره"],
    "industry": ["صنعت", "پتروشیمی", "فولاد", "خودرو", "بانک", "بیمه"],
    "political": ["مجلس", "دولت", "وزیر", "رئیس‌جمهور", "تحریم", "مذاکرات", "برجام"],
    "global": ["نفت", "طلا", "کامودیتی", "بازار جهانی", "فدرال رزرو", "اوپک"],
}

# Normalize Persian text: convert Arabic Yeh/Kaf to Persian
def normalize_persian(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("ﻻ", "لا")
        .replace("\u200c", " ")  # ZWNJ → space for matching
    )