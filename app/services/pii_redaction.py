"""
PII detection & redaction — runs BEFORE text is sent to the LLM API or
written to any log/metrics store. Uses Presidio (Microsoft's open-source
PII detection library), which wraps spaCy NER + pattern recognizers.

Explicitly configured to use spaCy's SMALL model (en_core_web_sm, ~50MB)
instead of Presidio's default LARGE model (en_core_web_lg, ~560MB) —
the large model alone exceeds Render's free-tier 512MB RAM budget and
was silently OOM-killing the whole process mid-pipeline. Small model is
slightly less accurate at entity detection but fits comfortably; if you
deploy somewhere with more RAM, swap "en_core_web_sm" back to "_lg" below
and in Dockerfile / README.

SCORE_THRESHOLD below is raised above Presidio's default (0.0, i.e. no
filtering) specifically because the small spaCy model has lower NER
precision and was observed misclassifying fragments of line-item
descriptions (e.g. part of "Gas Can (5 feet)") as PERSON entities,
corrupting line items before they ever reach the LLM. Raising the bar
trades a little recall on genuinely ambiguous names for far fewer false
positives on ordinary product text — the right tradeoff for invoice/
receipt line items, which are mostly non-personal-name text to begin with.
"""
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine

_analyzer = None
_anonymizer = None

PII_ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "IBAN_CODE"]
SCORE_THRESHOLD = 0.6

_NLP_CONFIG = {
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
}


def _get_engines():
    global _analyzer, _anonymizer
    if _analyzer is None:
        provider = NlpEngineProvider(nlp_configuration=_NLP_CONFIG)
        nlp_engine = provider.create_engine()
        _analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
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
    results = analyzer.analyze(
        text=text, entities=PII_ENTITIES, language="en", score_threshold=SCORE_THRESHOLD
    )
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results)

    pii_map = {r.entity_type: text[r.start:r.end] for r in results}
    return anonymized.text, pii_map
