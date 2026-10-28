"""Run once (optional - the app also does this automatically): python init_db.py"""
import database

database.init_db()
print(f"Database ready at {database.DB_PATH}")
