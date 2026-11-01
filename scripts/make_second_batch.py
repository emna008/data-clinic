"""Génère data/sales_2026-11.csv : le lot du mois suivant, même famille que sales_dirty.csv.

Il est globalement propre mais viole volontairement le contrat de données appris :
- un pays hors liste ('Portugal') et une variante connue ('Deutschland'),
- deux prix négatifs,
- une colonne renommée (unit_price -> price), ce qui casse le schéma attendu,
- un produit inconnu ('Webcam').
C'est le moment clé de la démo : l'Auditor doit signaler ces points comme violations du contrat.
"""
import random
from pathlib import Path

import pandas as pd

random.seed(7)
products = [("Laptop", 899.0), ("Headphones", 59.9), ("Mouse", 19.5), ("Monitor", 249.0), ("Keyboard", 45.0)]
countries = ["France", "Tunisia", "Germany", "Spain", "Italy"]
rows = []
for i in range(301, 381):
    name, price = random.choice(products)
    qty = random.randint(1, 5)
    d = pd.Timestamp("2026-11-01") + pd.Timedelta(days=random.randint(0, 29))
    rows.append({"order_id": f"ORD-{i:04d}", "order_date": d.strftime("%Y-%m-%d"),
                 "customer_email": f"client{i}@example.com", "country": random.choice(countries),
                 "product": name, "quantity": qty, "unit_price": price, "total": round(qty * price, 2)})
df = pd.DataFrame(rows)
df.loc[3, "country"] = "Portugal"
df.loc[11, "country"] = "Deutschland"
df.loc[[20, 45], "unit_price"] *= -1
df.loc[30, "product"] = "Webcam"
df.loc[30, "unit_price"] = 39.0
df.loc[30, "total"] = round(df.loc[30, "quantity"] * 39.0, 2)
df = df.rename(columns={"unit_price": "price"})
out = Path(__file__).resolve().parent.parent / "data" / "sales_2026-11.csv"
df.to_csv(out, index=False)
print(f"{len(df)} lignes écrites dans {out}")
