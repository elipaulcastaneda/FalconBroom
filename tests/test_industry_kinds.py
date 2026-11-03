from fbroom.semantics import value_kinds, header_kinds


def test_new_industry_kinds_are_recognised():
    assert 'ssn' in value_kinds('123-45-6789')
    assert 'card' in value_kinds('4111111111111111')
    assert 'icd' in value_kinds('E11.9')
    assert 'bloodpressure' in value_kinds('120/80')
    assert 'quantity' in value_kinds('12.5 kg')
    assert 'ip' in value_kinds('192.168.1.1')
    assert 'person' in value_kinds('Maria Garcia')
    assert value_kinds('N/A') == {'missing'}


def test_header_engine_uses_the_head_noun():
    assert 'phone' in header_kinds('contractor_phone') and 'person' not in header_kinds('contractor_phone')
    assert 'money' in header_kinds('estimated_cost_usd')
    assert 'temperature' in header_kinds('temp_f')
    assert 'place' not in header_kinds('city_id')
    assert header_kinds('real_estate') is None


def test_retail_hr_education_property_iot_kinds():
    assert 'term' in value_kinds('Fall 2023') and 'term' in value_kinds('2023-2024')
    assert 'course' in value_kinds('MATH 2210')
    assert 'duration' in value_kinds('2 days')
    assert 'carrier' in value_kinds('FedEx Ground')
    assert 'employment' in value_kinds('Part Time')
    assert 'uom' in value_kinds('ea')
    assert 'payment_method' in value_kinds('PayPal')
    assert 'property' in value_kinds('Single-Family')
    assert 'date' in value_kinds('2023-12')


def test_new_headers():
    assert 'person' in header_kinds('employee_name')
    assert 'date' in header_kinds('hire_date')
    assert 'percent' in header_kinds('humidity')
    assert 'carrier' in header_kinds('carrier')
    assert 'id' in header_kinds('device_id')


def test_clean_industry_columns_do_not_suggest_swaps(tmp_path):
    import random
    from fbroom.ingest import generate_plain_language_suggestions

    random.seed(5)
    carriers = ["UPS", "FedEx Ground", "USPS", "DHL"]
    pays = ["PayPal", "Visa", "Cash", "Apple Pay"]
    types = ["Full-Time", "Part Time", "Contract", "Intern"]
    depts = ["Sales", "Finance", "Legal", "Operations"]
    rows = ["carrier,payment,employment,dept,term,course"]
    for i in range(400):
        rows.append(",".join([
            random.choice(carriers), random.choice(pays), random.choice(types), random.choice(depts),
            random.choice(["Fall 2023", "Spring 2024", "2023-2024"]), f"CS{100 + i % 50}",
        ]))
    path = tmp_path / "industry.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    suggestions = generate_plain_language_suggestions({"path": str(path), "normalized_path": str(path)})
    assert not any("swap" in str(s.get("source", "")) for s in suggestions)
