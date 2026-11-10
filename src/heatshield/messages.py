"""Fixed multilingual application text for the synthetic demonstration.

These templates are reviewed application copy, not clinically validated guidance.
Values are inserted literally. Templates never evaluate expressions, select policy
thresholds, diagnose a person, or contact an emergency service.
"""

from types import MappingProxyType

SUPPORTED_LANGUAGES = ("en", "hi", "ar")

_TEMPLATES = {
    "recovery": {
        "en": (
            "SIMULATION: Take your planned protected recovery from {starts_at} to {ends_at} "
            "in the designated cooling area. Tell your supervisor if it is unavailable."
        ),
        "hi": (
            "सिमुलेशन: {starts_at} से {ends_at} तक निर्धारित शीतल विश्राम क्षेत्र में अपना "
            "नियोजित संरक्षित विश्राम लें। यदि वह क्षेत्र उपलब्ध नहीं है तो अपने पर्यवेक्षक को बताएं।"
        ),
        "ar": (
            "محاكاة: خذ فترة الراحة المحمية المقررة من {starts_at} إلى {ends_at} في منطقة "
            "التبريد المحددة. أخبر المشرف إذا لم تكن متاحة."
        ),
    },
    "stop_work": {
        "en": (
            "SIMULATION: Stop work and contact the trained on-site responder and supervisor "
            "immediately. Follow the site's emergency procedure. This application does not "
            "diagnose illness or contact emergency services."
        ),
        "hi": (
            "सिमुलेशन: काम रोकें और प्रशिक्षित स्थल आपातकालीन सहायता कर्मी तथा पर्यवेक्षक से "
            "तुरंत संपर्क करें। स्थल की आपातकालीन प्रक्रिया का पालन करें। यह एप्लिकेशन बीमारी "
            "का निदान नहीं करता और आपातकालीन सेवाओं से संपर्क नहीं करता।"
        ),
        "ar": (
            "محاكاة: أوقف العمل واتصل فوراً بالمستجيب المدرّب في الموقع وبالمشرف. اتبع إجراءات "
            "الطوارئ في الموقع. هذا التطبيق لا يشخّص المرض ولا يتصل بخدمات الطوارئ."
        ),
    },
    "acknowledge": {
        "en": (
            "SIMULATION: Your acknowledgement records that you received this instruction. "
            "It does not verify that the protective action happened."
        ),
        "hi": (
            "सिमुलेशन: आपकी पावती दर्ज करती है कि आपको यह निर्देश मिला है। इससे यह सत्यापित "
            "नहीं होता कि सुरक्षात्मक कार्रवाई की गई।"
        ),
        "ar": ("محاكاة: يسجّل إقرارك أنك تلقيت هذه التعليمات. ولا يؤكد تنفيذ الإجراء الوقائي."),
    },
    "verify": {
        "en": (
            "SIMULATION: Record supervisor verification only after checking that the planned "
            "protective action and designated cooling resources were provided."
        ),
        "hi": (
            "सिमुलेशन: नियोजित सुरक्षात्मक कार्रवाई और निर्धारित शीतलन संसाधन उपलब्ध कराए जाने "
            "की जांच के बाद ही पर्यवेक्षक सत्यापन दर्ज करें।"
        ),
        "ar": (
            "محاكاة: لا تسجّل تحقق المشرف إلا بعد التأكد من تنفيذ الإجراء الوقائي المخطط "
            "وتوفير موارد التبريد المحددة."
        ),
    },
}

TEMPLATES = MappingProxyType(
    {key: MappingProxyType(translations) for key, translations in _TEMPLATES.items()}
)
_FIELDS = {"recovery": frozenset({"starts_at", "ends_at"})}


def render_message(key: str, language: str = "en", **values: object) -> str:
    """Render a known simulation instruction with literal, bounded values.

    Unknown languages, templates, and placeholder names fail explicitly rather
    than silently changing the requested instruction or falling back to English.
    """
    if key not in TEMPLATES:
        raise ValueError(f"Unknown message key: {key}")
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported message language: {language}")
    expected = _FIELDS.get(key, frozenset())
    if values.keys() != expected:
        raise ValueError(
            f"Message {key} requires exactly these values: {', '.join(sorted(expected))}"
        )
    rendered_values = {}
    for name, value in values.items():
        text = str(value)
        if not text.strip() or len(text) > 160 or any(ord(char) < 32 for char in text):
            raise ValueError(f"Invalid message value: {name}")
        rendered_values[name] = text
    return TEMPLATES[key][language].format_map(rendered_values)
