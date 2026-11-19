import os
import pymysql
from dotenv import load_dotenv
from urllib.parse import urlparse, unquote

load_dotenv()

def diagnostic():
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("❌ DATABASE_URL is NOT set in environment.")
        return

    print(f"🔍 Found DATABASE_URL (masked): {db_url[:10]}...{db_url[-10:] if len(db_url) > 20 else ''}")
    
    try:
        parsed = urlparse(db_url)
        user = unquote(parsed.username)
        host = parsed.hostname
        port = parsed.port or 3306
        db = parsed.path.lstrip('/')
        
        print(f"📡 Target Host: {host}")
        print(f"👤 User: {user}")
        print(f"📦 Database: {db}")
        
        ssl_params = {'ssl': {'ca': None}} if 'ssl-mode=REQUIRED' in db_url else None
        
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=unquote(parsed.password),
            database=db,
            cursorclass=pymysql.cursors.DictCursor,
            ssl=ssl_params
        )
        
        with conn.cursor() as cursor:
            print("\n📊 Table Row Counts:")
            tables = ['users', 'categories', 'products', 'customers', 'sales']
            for table in tables:
                try:
                    cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
                    count = cursor.fetchone()['count']
                    print(f"   - {table}: {count}")
                except Exception as e:
                    print(f"   - {table}: Error ({e})")
            
            # Check products to see if they are sample products
            cursor.execute("SELECT name FROM products LIMIT 5")
            prods = cursor.fetchall()
            print("\n🍎 First 5 products:")
            for p in prods:
                print(f"   - {p['name']}")
                
        conn.close()
        print("\n✅ Connection successful!")
        
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")

if __name__ == "__main__":
    diagnostic()
