"""Generate synthetic CSV datasets demonstrating common data quality issues.
Creates files under `samples/test_datasets/`.
Run with the workspace Python (recommended .venv):

  .\.venv\Scripts\python.exe scripts\generate_test_csvs.py

"""
import os
import csv
import random
import uuid
from datetime import datetime, timedelta

random.seed(20260707)
BASE_DIR = os.path.join(os.path.dirname(__file__), '..', 'samples', 'test_datasets')
os.makedirs(BASE_DIR, exist_ok=True)

NAMES = [
    'Alice', 'Bob', 'Charlie', 'Diana', 'Eve', 'Frank', 'Grace', 'Heidi', 'Ivan', 'Judy',
    'Mallory', 'Niaj', 'Olivia', 'Peggy', 'Quentin', 'Rupert', 'Sybil', 'Trent', 'Uma', 'Victor'
]
CITIES = ['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix', 'Philadelphia', 'San Antonio', 'San Diego']
PRODUCTS = ['Widget', 'Gadget', 'Doohickey', 'Thingamajig', 'Gizmo']


def write_csv(fname, header, rows):
    path = os.path.join(BASE_DIR, fname)
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    print(f'Wrote {path} ({len(rows)} rows)')


# 1. Missing values in critical columns
def gen_missing_values(n=400):
    header = ['customer_id', 'name', 'email', 'signup_date', 'country']
    rows = []
    for i in range(n):
        cid = f'C{1000+i}'
        name = random.choice(NAMES) if random.random() > 0.05 else ''
        email = (name.lower() + f'{i}@example.com') if name and random.random() > 0.1 else ''
        sd = (datetime.now() - timedelta(days=random.randint(0, 500))).date().isoformat()
        country = random.choice(['US', 'GB', 'CA', 'AU', '']) if random.random() > 0.02 else ''
        rows.append([cid, name, email, sd, country])
    write_csv('customers_missing_values.csv', header, rows)

# 2. Inconsistent date formats
def gen_inconsistent_dates(n=500):
    header = ['order_id', 'product', 'order_date', 'delivery_date', 'amount']
    rows = []
    start = datetime.now() - timedelta(days=365)
    for i in range(n):
        oid = f'O{2000+i}'
        product = random.choice(PRODUCTS)
        od = start + timedelta(days=random.randint(0, 365))
        # mix formats
        if random.random() < 0.3:
            order_date = od.strftime('%Y-%m-%d')
        elif random.random() < 0.6:
            order_date = od.strftime('%m/%d/%Y')
        else:
            order_date = od.strftime('%d-%b-%y')
        # sometimes invalid
        if random.random() < 0.02:
            delivery_date = '2026-02-30'
        else:
            dd = od + timedelta(days=random.randint(1, 14))
            delivery_date = dd.strftime(random.choice(['%Y-%m-%d','%m/%d/%Y','%d/%m/%Y']))
        amount = f"${random.randint(10,500)}.{random.randint(0,99):02d}" if random.random() < 0.2 else str(round(random.uniform(10,500),2))
        rows.append([oid, product, order_date, delivery_date, amount])
    write_csv('sales_inconsistent_dates.csv', header, rows)

# 3. Duplicate product IDs
def gen_duplicate_ids(n=350):
    header = ['product_id', 'name', 'category', 'price']
    rows = []
    for i in range(n):
        pid = f'P{100 + (i % 50)}'  # force duplicates every 50
        name = random.choice(PRODUCTS)
        cat = random.choice(['tools','home','office'])
        price = round(random.uniform(5,200),2)
        rows.append([pid, name, cat, price])
    write_csv('products_duplicate_ids.csv', header, rows)

# 4. Incorrect types and noisy numbers
def gen_incorrect_types(n=450):
    header = ['order_id', 'quantity', 'unit_price', 'total']
    rows = []
    for i in range(n):
        oid = f'ORD{3000+i}'
        qty = random.choice([str(random.randint(1,20)), f"{random.randint(1,20)},000", 'ten', ''])
        up = random.choice([f"${random.uniform(1,100):.2f}", f"{random.uniform(1,100):.2f}", 'N/A'])
        try:
            total = ''
            if qty.isdigit() and up.startswith('$'):
                total = f"{int(qty) * float(up.replace('$','')):.2f}"
            else:
                total = ''
        except Exception:
            total = ''
        rows.append([oid, qty, up, total])
    write_csv('orders_incorrect_types.csv', header, rows)

# 5. Email noise and malformed
def gen_email_noise(n=320):
    header = ['user_id', 'name', 'email']
    rows = []
    for i in range(n):
        uid = str(uuid.uuid4())[:8]
        name = random.choice(NAMES)
        if random.random() < 0.15:
            email = name + ' at example dot com'
        elif random.random() < 0.15:
            email = f' {name.lower()}@EXAMPLE.COM '
        elif random.random() < 0.05:
            email = '@missinglocal'
        else:
            email = f'{name.lower()}.{i}@example.com'
        rows.append([uid, name, email])
    write_csv('users_email_noise.csv', header, rows)

# 6. Negative amounts and zeros
def gen_negative_amounts(n=380):
    header = ['txn_id', 'user', 'amount', 'currency']
    rows = []
    for i in range(n):
        tid = f'TX{4000+i}'
        user = random.choice(NAMES)
        amt = round(random.uniform(-500, 1000),2)
        # bias to positive but introduce negatives and zeros
        if random.random() < 0.1:
            amt = 0
        if random.random() < 0.05:
            amt = -abs(amt)
        rows.append([tid, user, amt, 'USD'])
    write_csv('transactions_negative_amounts.csv', header, rows)

# 7. Time series gaps and duplicate timestamps
def gen_time_series(n=600):
    header = ['sensor_id', 'timestamp', 'reading']
    rows = []
    base = datetime.now() - timedelta(hours=10)
    for i in range(n):
        sid = f'S{i%5+1}'
        # sometimes duplicate timestamp
        if random.random() < 0.05:
            ts = base + timedelta(seconds=(i-1)*30)
        else:
            ts = base + timedelta(seconds=i*30 + (random.choice([0,0,5,7,60]) if random.random()<0.2 else 0))
        reading = round(random.uniform(0,100),2)
        rows.append([sid, ts.isoformat(), reading])
    write_csv('sensor_time_gaps.csv', header, rows)

# 8. Demographics outliers and typos
def gen_demographics(n=420):
    header = ['person_id', 'age', 'height_cm', 'weight_kg']
    rows = []
    for i in range(n):
        pid = f'PR{5000+i}'
        if random.random() < 0.03:
            age = random.choice(['-1','200','twenty'])
        else:
            age = random.randint(0,95)
        height = round(random.uniform(120,210),1)
        if random.random() < 0.02:
            height = f"{height}cm"
        weight = round(random.uniform(40,150),1)
        rows.append([pid, age, height, weight])
    write_csv('demographics_outliers.csv', header, rows)

# 9. Addresses inconsistent
def gen_addresses(n=340):
    header = ['id', 'address_line1', 'address_line2', 'city', 'postal_code']
    rows = []
    for i in range(n):
        idd = f'A{6000+i}'
        if random.random() < 0.2:
            # put full address in line1
            line1 = f'{random.randint(1,999)} {random.choice(CITIES)} Ave, Apt {random.randint(1,999)}'
            line2 = ''
        else:
            line1 = f'{random.randint(1,999)} {random.choice(["Main St","Broadway","Oak Rd"]) }'
            line2 = f'Apt {random.randint(1,999)}' if random.random() < 0.4 else ''
        city = random.choice(CITIES) if random.random() > 0.05 else ''
        postal = ''.join(random.choices('0123456789', k=5)) if random.random() > 0.05 else 'XXXXX'
        rows.append([idd, line1, line2, city, postal])
    write_csv('addresses_inconsistent_formats.csv', header, rows)

# 10. Mixed units
def gen_mixed_units(n=360):
    header = ['sku', 'quantity', 'unit']
    rows = []
    for i in range(n):
        sku = f'SKU{7000+i}'
        q = random.randint(1,1000)
        if random.random() < 0.2:
            unit = random.choice(['kg','g','lbs','pieces','pcs'])
            # sometimes quantity given with unit glued
            if random.random() < 0.15:
                qty = f"{q}{unit}"
            else:
                qty = q
        else:
            unit = random.choice(['pcs','units'])
            qty = q
        rows.append([sku, qty, unit])
    write_csv('inventory_mixed_units.csv', header, rows)

# 11. Feedback multilingual and noisy
def gen_feedback(n=300):
    header = ['feedback_id', 'user', 'comment']
    rows = []
    snippets = [
        'Great product!', 'Needs improvement', '<b>Broken</b>', '👍👍', 'No funciona', 'Très bien', 'ありがとう', 'See\\nnote', 'bad\\nservice']
    for i in range(n):
        fid = f'F{8000+i}'
        user = random.choice(NAMES)
        comment = random.choice(snippets)
        # inject HTML or long multi-line randomly
        if random.random() < 0.05:
            comment = '<script>alert(1)</script>'
        rows.append([fid, user, comment])
    write_csv('feedback_multilingual_noise.csv', header, rows)

# 12. Employee ID inconsistencies
def gen_employee_ids(n=460):
    header = ['emp_id', 'name', 'department']
    rows = []
    for i in range(n):
        fmt = random.choice(['%05d','%d','EMP-%04d','%s'])
        if fmt == '%s':
            eid = str(uuid.uuid4())[:6]
        else:
            eid = fmt % (100 + i)
        rows.append([eid, random.choice(NAMES), random.choice(['eng','sales','hr','ops'])])
    write_csv('employees_inconsistent_ids.csv', header, rows)


if __name__ == '__main__':
    gen_missing_values(400)
    gen_inconsistent_dates(500)
    gen_duplicate_ids(350)
    gen_incorrect_types(450)
    gen_email_noise(320)
    gen_negative_amounts(380)
    gen_time_series(600)
    gen_demographics(420)
    gen_addresses(340)
    gen_mixed_units(360)
    gen_feedback(300)
    gen_employee_ids(460)
    print('\nGeneration complete.')

    # --- Additional combined-issue datasets ---
    def gen_missing_and_duplicates(n=420):
        header = ['user_id', 'email', 'signup_date', 'referrer']
        rows = []
        for i in range(n):
            uid = f'U{1000 + (i % 180)}'  # duplicates introduced
            name = random.choice(NAMES)
            if random.random() < 0.12:
                email = ''  # missing
            else:
                email = f'{name.lower()}.{i}@example.com'
            if random.random() < 0.08:
                sd = ''
            else:
                sd = (datetime.now() - timedelta(days=random.randint(0,1000))).date().isoformat()
            ref = random.choice(['google','friend','ad',''])
            rows.append([uid, email, sd, ref])
        write_csv('combined_missing_duplicates.csv', header, rows)

    def gen_dates_and_bad_types(n=520):
        header = ['id', 'event_date', 'value']
        rows = []
        for i in range(n):
            eid = f'E{2000+i}'
            # mix ISO, slashed, textual, and intentionally malformed
            d = datetime.now() - timedelta(days=random.randint(0,1000))
            fmt = random.random()
            if fmt < 0.25:
                ed = d.strftime('%Y-%m-%dT%H:%M:%SZ')
            elif fmt < 0.5:
                ed = d.strftime('%m/%d/%Y')
            elif fmt < 0.75:
                ed = d.strftime('%d %b %Y')
            else:
                ed = 'not a date'
            # value sometimes non-numeric or with text
            if random.random() < 0.12:
                val = 'N/A'
            elif random.random() < 0.08:
                val = f'{random.randint(1,1000)} units'
            else:
                val = round(random.uniform(0,1000),2)
            rows.append([eid, ed, val])
        write_csv('combined_dates_badtypes.csv', header, rows)

    def gen_encoding_and_specialchars(n=340):
        header = ['id', 'comment']
        rows = []
        extras = ['naïve', 'résumé', 'café', '北京', '😀', 'olá', 'coöperate']
        for i in range(n):
            cid = f'CMT{3000+i}'
            text = random.choice(['OK', 'error', '<div>bad</div>', ''])
            if random.random() < 0.2:
                text = random.choice(extras)
            # inject embedded newlines and commas and quotes
            if random.random() < 0.08:
                text = f'Line1\nLine2, extra "quoted" text'
            rows.append([cid, text])
        write_csv('combined_encoding_specialchars.csv', header, rows)

    def gen_extra_columns_and_truncated(n=360):
        header = ['rec_id', 'field_a', 'field_b']
        rows = []
        for i in range(n):
            rid = f'R{4000+i}'
            a = random.choice(NAMES)
            b = random.choice(CITIES)
            # sometimes extra unexpected columns
            if random.random() < 0.12:
                rows.append([rid, a, b, 'EXTRA_COL', 'ANOTHER'])
            elif random.random() < 0.08:
                # truncated row with missing fields
                rows.append([rid, a])
            else:
                rows.append([rid, a, b])
        write_csv('combined_extra_truncated.csv', header, rows)

    def gen_html_and_multilang(n=300):
        header = ['id', 'comment']
        rows = []
        examples = ['Great!', 'No funciona', '很好', '<b>bold</b>', '<img src=x onerror=1>', 'Merci', 'ありがとう']
        for i in range(n):
            cid = f'H{5000+i}'
            c = random.choice(examples)
            # sometimes long concatenated or repeated HTML
            if random.random() < 0.06:
                c = c + ' ' + c + ' <script>bad()</script>'
            rows.append([cid, c])
        write_csv('combined_html_multilang.csv', header, rows)

    def gen_whitespace_and_case(n=380):
        header = ['username', 'email']
        rows = []
        for i in range(n):
            uname = random.choice(NAMES)
            if random.random() < 0.12:
                uname = ' ' + uname + ' '
            if random.random() < 0.08:
                uname = uname.upper()
            email = f'{uname.strip().lower()}@example.com'
            if random.random() < 0.1:
                email = ' ' + email + ' '
            rows.append([uname, email])
        write_csv('combined_whitespace_case.csv', header, rows)

    def gen_currency_and_negatives(n=420):
        header = ['txn', 'amount']
        rows = []
        for i in range(n):
            tid = f'CN{6000+i}'
            if random.random() < 0.15:
                amt = f'(${random.uniform(1,500):.2f})'  # accounting negative parens
            elif random.random() < 0.12:
                amt = f'-{round(random.uniform(1,500),2)}'
            elif random.random() < 0.08:
                amt = f'{random.randint(1,500)} USD'
            else:
                amt = f'${random.uniform(1,500):.2f}'
            rows.append([tid, amt])
        write_csv('combined_currency_negatives.csv', header, rows)

    # generate the combined datasets
    gen_missing_and_duplicates(420)
    gen_dates_and_bad_types(520)
    gen_encoding_and_specialchars(340)
    gen_extra_columns_and_truncated(360)
    gen_html_and_multilang(300)
    gen_whitespace_and_case(380)
    gen_currency_and_negatives(420)
