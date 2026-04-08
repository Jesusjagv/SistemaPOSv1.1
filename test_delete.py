import requests

BASE_URL = "http://localhost:5000/api"

def test_implementation():
    # 1. Login
    print("--- Test 1: Login ---")
    resp = requests.post(f"{BASE_URL}/auth/login", json={"username": "admin", "password": "admin123"})
    if resp.status_code != 200:
        print(f"FAILED login: {resp.text}")
        return
    token = resp.json().get('token')
    headers = {"Authorization": f"Bearer {token}"}
    print("SUCCESS: Logged in")

    # 2. Create Test Product
    print("\n--- Test 2: Create & Delete New Product ---")
    prod_data = {
        "name": "Test Delete Me",
        "code": "TEST-DEL-999",
        "price_usd": 10.50,
        "stock": 5
    }
    resp = requests.post(f"{BASE_URL}/products", json=prod_data, headers=headers)
    if resp.status_code != 201:
        print(f"FAILED creation: {resp.text}")
        return
    prod_id = resp.json().get('id')
    print(f"SUCCESS: Created product ID {prod_id}")

    # 3. Delete the product
    resp = requests.delete(f"{BASE_URL}/products/{prod_id}", headers=headers)
    if resp.status_code == 200:
        print(f"SUCCESS: Product deleted. Message: {resp.json().get('message')}")
    else:
        print(f"FAILED deletion: {resp.text}")

    # 4. Try to delete a product with sales (Agua Mineral ID=1 usually)
    print("\n--- Test 3: Delete Product with Sales (Expected to Fail) ---")
    # First, let's make sure it has a sale or just try ID 1 (Agua Mineral)
    # The default DB has sample products. Let's try ID 1.
    resp = requests.delete(f"{BASE_URL}/products/1", headers=headers)
    if resp.status_code == 400:
        print(f"SUCCESS: Deletion blocked as expected. Message: {resp.json().get('error')}")
    elif resp.status_code == 200:
        print("WARNING: Product with potential sales was deleted! (Check if ID 1 had sales)")
    else:
        print(f"RESULT: Status {resp.status_code}, Msg: {resp.text}")

if __name__ == "__main__":
    test_implementation()
