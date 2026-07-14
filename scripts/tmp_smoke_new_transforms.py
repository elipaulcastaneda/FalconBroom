from fbroom import engine
print('has_parse_address:', hasattr(engine, '_parse_address_column'))
print('has_phone_norm:', hasattr(engine, '_normalize_phone_column'))
print('has_date_parse:', hasattr(engine, '_robust_date_parse_column'))
print('has_unit_conv:', hasattr(engine, '_convert_units_column'))
print('has_currency_conv:', hasattr(engine, '_convert_currency_column'))
