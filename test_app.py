import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.database import init_db, get_connection
init_db()

conn = get_connection()
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM properties")
count = cursor.fetchone()[0]
print(f"Properties in database: {count}")

cursor.execute("SELECT title, purchase_price, gross_yield, monthly_cashflow, source_portal FROM properties LIMIT 3")
for row in cursor.fetchall():
    print(f"- {row[4]}: {row[0]} | Price: {row[1]} € | Yield: {row[2]}% | Cashflow: {row[3]} €")

conn.close()

from app.main import app
print("FastAPI app successfully initialized!")
