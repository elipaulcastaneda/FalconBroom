import csv
import random
from datetime import datetime, timedelta

def daterange(start, n):
    for i in range(n):
        yield start + timedelta(days=i)

def make_customers(n):
    first = ['Olivia','Liam','Emma','Noah','Ava','William','Sophia','James','Isabella','Benjamin','Mia','Lucas','Amelia','Mason','Harper']
    last = ['Smith','Johnson','Williams','Jones','Brown','Davis','Miller','Wilson','Moore','Taylor','Anderson','Thomas','Jackson','White','Harris']
    start = datetime(2020,1,1)
    rows = []
    for i in range(1, n+1):
        fn = random.choice(first)
        ln = random.choice(last)
        email = f"{fn.lower()}.{ln.lower()}{i}@example.com"
        d = random.choice(list(daterange(start, 600))).date().isoformat()
        rows.append({'customer_id': i, 'first_name': fn, 'last_name': ln, 'email': email, 'signup_date': d})
    return rows

def make_orders(n, customer_count):
    rows = []
    for i in range(1, n+1):
        customer_id = random.randint(1, customer_count)
        order_id = i
        amt = round(random.uniform(10, 500), 2)
        days = random.randint(0, 900)
        d = (datetime(2020,1,1) + timedelta(days=days)).date().isoformat()
        rows.append({'order_id': order_id, 'customer_id': customer_id, 'order_date': d, 'amount': amt})
    return rows

def make_payments(n, order_count, customer_count):
    rows = []
    for i in range(1, n+1):
        order_id = random.randint(1, order_count)
        customer_id = random.randint(1, customer_count)
        method = random.choice(['card','paypal','bank_transfer'])
        status = random.choice(['paid','pending','failed'])
        amt = round(random.uniform(5, 500), 2)
        days = random.randint(0, 900)
        d = (datetime(2020,1,1) + timedelta(days=days)).date().isoformat()
        rows.append({'payment_id': i, 'order_id': order_id, 'customer_id': customer_id, 'payment_date': d, 'amount': amt, 'method': method, 'status': status})
    return rows

def write_csv(path, rows, fieldnames):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

if __name__ == '__main__':
    random.seed(42)
    customers = make_customers(300)
    orders = make_orders(400, 300)
    payments = make_payments(350, 400, 300)
    write_csv('samples/test_datasets/customers.csv', customers, ['customer_id','first_name','last_name','email','signup_date'])
    write_csv('samples/test_datasets/orders.csv', orders, ['order_id','customer_id','order_date','amount'])
    write_csv('samples/test_datasets/payments.csv', payments, ['payment_id','order_id','customer_id','payment_date','amount','method','status'])
    print('Wrote samples/test_datasets/{customers,orders,payments}.csv')
