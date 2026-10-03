#!/usr/bin/env python3
import os
import csv
from datetime import datetime, date, timedelta

OUT_DIR = os.path.join('samples', 'test_data')
os.makedirs(OUT_DIR, exist_ok=True)

# Create 300 shared event IDs
event_ids = [f"EVT{i:04d}" for i in range(1, 301)]

# Badger sightings
badger_path = os.path.join(OUT_DIR, 'badger_sightings.csv')
with open(badger_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['event_id', 'species', 'lat', 'lon', 'count', 'observed_at'])
    for i, eid in enumerate(event_ids):
        lat = 51.00000 + (i * 0.00543)
        lon = -0.10000 - (i * 0.00421)
        count = 1 + (i % 4)
        observed_at = (datetime(2025, 1, 1, 6, 0) + timedelta(minutes=i * 10)).isoformat()
        w.writerow([eid, 'badger', f"{lat:.5f}", f"{lon:.5f}", count, observed_at])

# Wolverine sightings
wolverine_path = os.path.join(OUT_DIR, 'wolverine_sightings.csv')
with open(wolverine_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['event_id', 'species', 'lat', 'lon', 'group_size', 'observed_at'])
    for i, eid in enumerate(event_ids):
        lat = 52.50000 + (i * 0.00612)
        lon = -1.25000 - (i * 0.00387)
        group_size = 1 + ((i + 1) % 3)
        observed_at = (datetime(2025, 1, 1, 7, 30) + timedelta(minutes=i * 12)).isoformat()
        w.writerow([eid, 'wolverine', f"{lat:.5f}", f"{lon:.5f}", group_size, observed_at])

# Weather conditions by date (YYYY-MM-DD)
weather_path = os.path.join(OUT_DIR, 'weather_2025_daily.csv')
start_date = date(2025, 1, 1)
conditions = ['Sunny', 'Cloudy', 'Rain', 'Snow', 'Fog', 'Windy', 'Storm']
with open(weather_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['date', 'precipitation_mm', 'temperature_c', 'wind_kph', 'condition'])
    for i in range(300):
        d = start_date + timedelta(days=i)
        precipitation = round(((i * 3) % 11) * 0.7, 1)
        temperature = round(5 + ((i * 7) % 15) * 0.8 - ((i % 30) / 10), 1)
        wind = round(5 + (i % 20) * 1.5, 1)
        condition = conditions[i % len(conditions)]
        w.writerow([d.isoformat(), precipitation, temperature, wind, condition])

# Traffic conditions by date (YYYY-MM-DD)
traffic_path = os.path.join(OUT_DIR, 'traffic_2025_daily.csv')
with open(traffic_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['date', 'traffic_volume', 'avg_speed_kph', 'incidents'])
    for i in range(300):
        d = start_date + timedelta(days=i)
        traffic_volume = 500 + ((i * 37) % 4500)
        avg_speed = round(max(10, 80 - (i % 30) * 0.8), 1)
        incidents = 1 if (i % 23 == 0) else 0
        w.writerow([d.isoformat(), traffic_volume, avg_speed, incidents])

print('Created files:')
print(' -', badger_path)
print(' -', wolverine_path)
print(' -', weather_path)
print(' -', traffic_path)
