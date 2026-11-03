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
