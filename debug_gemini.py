import os
import logging
from google import genai
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=api_key, http_options={'api_version': 'v1'})
print("--- V1 Models ---")
try:
    for m in client.models.list():
        print(m.name)
except Exception as e:
    print(f"V1 List failed: {e}")

client_beta = genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})
print("\n--- V1Beta Models ---")
try:
    for m in client_beta.models.list():
        print(m.name)
except Exception as e:
    print(f"V1Beta List failed: {e}")
