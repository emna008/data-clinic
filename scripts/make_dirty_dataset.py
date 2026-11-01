"""Génère data/sales_dirty.csv : un dataset e-commerce réaliste avec des défauts injectés.
Défauts : dates en 3 formats, doublons, prix négatifs, emails invalides, pays mal orthographiés,
valeurs manquantes, quantités aberrantes, espaces parasites.
"""
import random
from pathlib import Path

import pandas as pd

random.seed(42)
N = 300
countries = ["France", "Tunisia", "Germany", "Spain", "Italy"]
typos = {"France": ["france", "FRANCE", "Frnace"], "Tunisia": ["tunisia", "Tunisie"], "Germany": ["germany", "Deutschland"]}
products = [("Laptop", 899.0), ("Headphones", 59.9), ("Mouse", 19.5), ("Monitor", 249.0), ("Keyboard", 45.0)]

rows = []
for i in range(1, N + 1):
    name, price = random.choice(products)
    qty = random.randint(1, 5)
    country = random.choice(countries)
    d = pd.Timestamp("2026-01-01") + pd.Timedelta(days=random.randint(0, 270))
    fmt = random.choice(["%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y"])
    email = f"client{i}@example.com"
    rows.append({
        "order_id": f"ORD-{i:04d}",
        "order_date": d.strftime(fmt),
        "customer_email": email,
        "country": country,
        "product": name,
        "quantity": qty,
        "unit_price": price,
        "total": round(qty * price, 2),
    })

df = pd.DataFrame(rows)

# Injection de défauts
idx = df.sample(frac=0.08, random_state=1).index
df.loc[idx, "country"] = df.loc[idx, "country"].map(lambda c: random.choice(typos.get(c, [c])))
df.loc[df.sample(frac=0.05, random_state=2).index, "customer_email"] = "not-an-email"
df.loc[df.sample(frac=0.04, random_state=3).index, "unit_price"] *= -1
df.loc[df.sample(frac=0.06, random_state=4).index, "customer_email"] = None
df.loc[df.sample(frac=0.03, random_state=5).index, "quantity"] = 999
df.loc[df.sample(frac=0.05, random_state=6).index, "product"] = df["product"].map(lambda p: f"  {p} ")
df = pd.concat([df, df.sample(n=12, random_state=7)], ignore_index=True)  # doublons exacts
df = df.sample(frac=1, random_state=8).reset_index(drop=True)

out = Path(__file__).resolve().parent.parent / "data" / "sales_dirty.csv"
df.to_csv(out, index=False)
print(f"{len(df)} lignes écrites dans {out}")
