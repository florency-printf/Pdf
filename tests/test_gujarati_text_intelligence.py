from app.utils.gujarati_text_intelligence import (
    correct_gujarati_text,
    extract_structured_fields,
    normalize_gujarati_text,
    validate_structured_fields,
)


def test_normalize_gujarati_text_removes_hidden_controls():
    text = "પ\u0003ઢીનું\u200b"
    result = normalize_gujarati_text(text)
    assert "\u0003" not in result
    assert "\u200b" not in result


def test_correct_gujarati_text_applies_dictionary_repair():
    text = "ગધિનગર ખાતે બેઠક યોજાઈ."
    result = correct_gujarati_text(text)
    assert "ગાંધીનગર" in result


def test_extract_structured_fields_parses_common_values():
    text = """
    તા. 22-05-1980
    સર્વે નં. 590
    મ્યુટેશન નં. 18
    નોટિસ નં. 7
    જિલ્લો: ગાંધીનગર
    તાલુકો: ગાંધીનગર
    ગામ: ભાટ
    """
    fields = extract_structured_fields(text)
    assert "22-05-1980" in fields["dates"]
    assert "590" in fields["survey_numbers"]
    assert "18" in fields["mutation_numbers"]
    assert "7" in fields["notice_numbers"]
    assert "ગાંધીનગર" in fields["locations"]["districts"]
    assert fields["confidence"] > 0


def test_validate_structured_fields_reports_missing_context():
    fields = extract_structured_fields("")
    issues = validate_structured_fields(fields)
    assert issues
