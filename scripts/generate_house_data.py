"""Generate sample house construction CSVs for testing the Source tab diagnostics.
Creates 10 files in samples/test_data named generated_house_data_01.csv ... _10.csv
Each file has 13 columns: 12 expected + a stray zipcode column with injected errors.
"""
import csv
import random
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / 'samples' / 'test_data'
OUT_DIR.mkdir(parents=True, exist_ok=True)

NUM_FILES = 10
ROWS_PER_FILE = 500
SEED = 12345
random.seed(SEED)

states = ['CA','NY','TX','WA','IL','FL','PA','OH','MI','GA']
cities = {
    'CA': ['San Francisco','Los Angeles','San Diego','Sacramento','Fresno'],
    'NY': ['New York','Buffalo','Rochester','Syracuse','Albany'],
    'TX': ['Houston','Austin','Dallas','San Antonio','El Paso'],
    'WA': ['Seattle','Spokane','Tacoma','Vancouver','Bellevue'],
    'IL': ['Chicago','Aurora','Naperville','Springfield','Peoria'],
    'FL': ['Miami','Orlando','Tampa','Jacksonville','Tallahassee'],
    'PA': ['Philadelphia','Pittsburgh','Harrisburg','Allentown','Erie'],
    'OH': ['Columbus','Cleveland','Cincinnati','Toledo','Akron'],
    'MI': ['Detroit','Grand Rapids','Warren','Lansing','Flint'],
    'GA': ['Atlanta','Savannah','Augusta','Columbus','Athens']
}
materials = ['Wood','Brick','Concrete','Steel','Mixed']
contractors = ['BuildRight LLC','Acme Constructions','NorthStar Builders','Homestead Co','PrimeConstruct']
permit_statuses = ['issued','pending','denied','unknown']

# helper to create plausible zip codes
def gen_zip(state):
    # 70% valid 5-digit, 10% 9-digit, 20% invalid variants
    r = random.random()
    if r < 0.7:
        return f"{random.randint(10000, 99999):05d}"
    elif r < 0.8:
        return f"{random.randint(10000,99999):05d}-{random.randint(1000,9999):04d}"
    else:
        # invalid variants: short, letters, city name, or empty
        choice = random.choice(['short','alpha','city','empty','mixed'])
        if choice == 'short':
            return f"{random.randint(1000,9999):04d}"
        if choice == 'alpha':
            return ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ', k=5))
        if choice == 'city':
            return random.choice(random.choice(list(cities.values())))
        if choice == 'empty':
            return ''
        return f"{random.randint(1000,99999)}X"

# Introduce swapped-column errors at a controlled rate
SWAP_RATE = 0.08  # base swap rate for general files
RANDOM_INCORRECT_RATE = 0.05

# For stronger, targeted error injection per-file we use explicit patterns.
# File 01 will have heavy intentional errors (25% each) for three categories;
# other files get rotated similar patterns so the whole test set covers cases.
TARGET_RATE = 0.25
PATTERN_SETS = [
    ['zipcode_wrong_state', 'city_address_swap', 'buildingid_state_swap'],
    ['city_address_swap', 'buildingid_state_swap', 'zipcode_wrong_state'],
    ['buildingid_state_swap', 'zipcode_wrong_state', 'city_address_swap'],
]

for fi in range(1, NUM_FILES+1):
    fname = OUT_DIR / f"generated_house_data_{fi:02d}.csv"
    headers = [
        'building_id', 'address', 'city', 'state', 'construction_year', 'floors', 'area_sqft',
        'bedrooms', 'bathrooms', 'material', 'contractor', 'permit_status', 'zipcode'
    ]
    with fname.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for i in range(ROWS_PER_FILE):
            state = random.choice(states)
            city = random.choice(cities[state])
            building_id = f"B{fi:02d}{i:04d}"
            address = f"{random.randint(100,9999)} {random.choice(['Oak','Maple','Pine','Cedar','Elm','Main','Market'])} St"
            construction_year = random.randint(1950, 2023)
            floors = random.choices([1,2,3,4,5], weights=[30,45,15,7,3])[0]
            area_sqft = random.randint(600, 8000)
            bedrooms = random.choices([0,1,2,3,4,5], weights=[1,10,40,30,15,4])[0]
            bathrooms = max(1, int(bedrooms * 0.75) + random.choice([0,1]))
            material = random.choice(materials)
            contractor = random.choice(contractors)
            permit_status = random.choice(permit_statuses)
            zipcode = gen_zip(state)

            row = {
                'building_id': building_id,
                'address': address,
                'city': city,
                'state': state,
                'construction_year': construction_year,
                'floors': floors,
                'area_sqft': area_sqft,
                'bedrooms': bedrooms,
                'bathrooms': bathrooms,
                'material': material,
                'contractor': contractor,
                'permit_status': permit_status,
                'zipcode': zipcode
            }

            # introduce swapped-column errors
            # For file 01 and others we may apply stronger targeted patterns
            if fi == 1:
                # apply each target pattern independently at TARGET_RATE (25%)
                if random.random() < TARGET_RATE:
                    # zipcode from wrong state
                    other_state = random.choice([s for s in states if s != state])
                    row['zipcode'] = gen_zip(other_state)
                if random.random() < TARGET_RATE:
                    # swap city and address values
                    row['city'], row['address'] = row['address'], row['city']
                if random.random() < TARGET_RATE:
                    # swap building_id and state values
                    row['building_id'], row['state'] = row['state'], row['building_id']
            else:
                # for other files: occasionally apply base random swaps
                if random.random() < SWAP_RATE:
                    # pick a type of swap: city<->zipcode, zipcode->contractor, numeric->text, etc
                    swap_type = random.choice(['city_zip','zip_city','zip_wrongcol','city_in_zip','numeric_in_text'])
                    if swap_type == 'city_zip':
                        # place zipcode into city and city into zipcode
                        row['city'], row['zipcode'] = row['zipcode'], row['city']
                    elif swap_type == 'zip_city':
                        # put city string into zipcode
                        row['zipcode'] = row['city']
                    elif swap_type == 'zip_wrongcol':
                        # move zipcode into address (wrong column) and set zipcode to short/empty
                        row['address'] = row['address'] + f" Apt {random.randint(1,999)}"
                        row['address'] = f"{row['zipcode']} {row['address']}"
                        row['zipcode'] = ''
                    elif swap_type == 'city_in_zip':
                        # put similar city from different state into zipcode column
                        other_state = random.choice([s for s in states if s != state])
                        row['zipcode'] = random.choice(cities[other_state])
                    elif swap_type == 'numeric_in_text':
                        # put numeric area into contractor or material
                        row['contractor'] = str(row['area_sqft'])

            # random incorrect values in some fields
            if random.random() < RANDOM_INCORRECT_RATE:
                bad_choice = random.choice(['material_typos','contractor_code','state_lower','year_text'])
                if bad_choice == 'material_typos':
                    row['material'] = random.choice(['Wod', 'Brik', 'C0ncrete', 'Sttel'])
                elif bad_choice == 'contractor_code':
                    row['contractor'] = f"C-{random.randint(100,999)}"
                elif bad_choice == 'state_lower':
                    row['state'] = row['state'].lower()
                elif bad_choice == 'year_text':
                    row['construction_year'] = str(row['construction_year']) + 'yrs'

            writer.writerow(row)

    print(f"Wrote {fname}")

print('Done')
