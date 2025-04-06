from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
from pinecone import Pinecone, ServerlessSpec

from dotenv import load_dotenv
load_dotenv()


app = Flask(__name__)
CORS(app)

# Environment Variables
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ENV = os.getenv("PINECONE_ENV")  # e.g., 'us-west-2'
INDEX_NAME = "drug-info-index"

# Create an instance of Pinecone
pc = Pinecone(api_key=PINECONE_API_KEY)

# Check if the index exists; if not, create it.
indexes = pc.list_indexes()
if INDEX_NAME not in indexes.names():
    pc.create_index(
         name=INDEX_NAME,
         dimension=1536,  # Ensure this matches your embedding dimension
         metric='euclidean',
         spec=ServerlessSpec(
             cloud='aws',
             region=PINECONE_ENV
         )
    )

# Get a handle on the index
index = pc.Index(INDEX_NAME)

# Gemini API configuration
GEMINI_API_URL = "https://api.gemini.com/v1/generate"  # Adjust if needed
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def query_gemini_api(brand_drug_name, drug_info):
    prompt = (
         f"Given the following drug information for brand drug '{brand_drug_name}': {drug_info}, "
         "provide the generic drug name along with detailed comparison information."
    )
    payload = {
         "prompt": prompt,
         "max_tokens": 150
    }
    headers = {
         "Authorization": f"Bearer {GEMINI_API_KEY}",
         "Content-Type": "application/json"
    }
    response = requests.post(GEMINI_API_URL, json=payload, headers=headers)
    if response.status_code == 200:
         data = response.json()
         return data.get("generated_text", "No text returned")
    else:
         return f"Error querying Gemini API: {response.text}"

@app.route('/search', methods=['POST'])
def search():
    data = request.get_json()
    brand_drug = data.get("brand_drug", "").strip()
    if not brand_drug:
         return jsonify({"error": "No brand drug provided"}), 400

    # Query Pinecone for drug info (this is a placeholder query)
    query_result = index.query(vector=[0.0], top_k=1, filter={"brand_name": brand_drug})
    if query_result.get('matches'):
         drug_info = query_result['matches'][0]['metadata'].get("drug_info", "No drug info available")
    else:
         drug_info = "No information found for this drug"

    # Use Gemini API to generate generic drug info and comparisons
    generic_info = query_gemini_api(brand_drug, drug_info)

    return jsonify({
         "brand_drug": brand_drug,
         "generic_info": generic_info,
         "raw_info": drug_info
    })

if __name__ == '__main__':
    app.run(debug=True)
