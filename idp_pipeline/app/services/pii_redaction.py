"""
PII detection & redaction — runs BEFORE text is sent to the LLM API or
written to any log/metrics store. Uses Presidio (Microsoft's open-source
PII detection library), which wraps spaCy NER + pattern recognizers.
"""
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

_analyzer = None
_anonymizer = None

PII_ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "IBAN_CODE"]


def _get_engines():
    global _analyzer, _anonymizer
    if _analyzer is None:
        _analyzer = AnalyzerEngine()
        _anonymizer = AnonymizerEngine()
    return _analyzer, _anonymizer


def redact(text: str) -> tuple[str, dict]:
    """
    Returns (redacted_text, pii_map). pii_map is the original values keyed
    by entity type — store this ONLY in an access-controlled, encrypted
    location if you ever need it for audit; never alongside the redacted
    text used for normal processing/logging.
    """
    if not text or not text.strip():
        return text, {}

    analyzer, anonymizer = _get_engines()
    results = analyzer.analyze(text=text, entities=PII_ENTITIES, language="en")
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results)

    pii_map = {r.entity_type: text[r.start:r.end] for r in results}
    return anonymized.text, pii_map
