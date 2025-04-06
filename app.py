from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
import re
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

# CORS configuration: allow requests from the local frontend.
CORS(app, resources={
    r"/*": {
        "origins": "http://127.0.0.1:5500",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# Environment Variables and Pinecone index setup.
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ENV = os.getenv("PINECONE_ENV")  # Example: 'us-west-2'
INDEX_NAME = "sfhacks3"

# Create an instance of Pinecone.
pc = Pinecone(api_key=PINECONE_API_KEY)
indexes = pc.list_indexes()
if INDEX_NAME not in indexes.names():
    pc.create_index(
         name=INDEX_NAME,
         dimension=1536,  # Ensure this matches your embedding dimension
         metric='euclidean',
         spec=ServerlessSpec(cloud='aws', region=PINECONE_ENV)
    )
index = pc.Index(INDEX_NAME)

# Gemini API configuration for normalization and formatting.
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def extract_generic_info(raw_info: str) -> dict:
    """
    Extracts metadata from a standardized semicolon-delimited key–value string.
    Expected format (for brand_drug records):
      "Brand: X; Manufacturer: Y; Ingredient: Z; Usage: ...; Price: ...; Side Effects: ..."
    """
    data = {}
    for field in raw_info.split(";"):
        if ":" in field:
            key, value = field.split(":", 1)
            data[key.strip().lower()] = value.strip()
    
    # Try to get the ingredient from either "ingredient" or "active ingredient"
    ingredient = data.get("ingredient", data.get("active ingredient", "Not found"))
    manufacturer = data.get("manufacturer", "Not found")
    
    price_field = data.get("price", "Not found")
    m = re.search(r"(\$\d+\.\d+).*?(for\s+.+)", price_field, re.IGNORECASE)
    if m:
        price = m.group(1).strip()
        dosage = m.group(2).strip()
    else:
        price = price_field
        dosage = ""
    
    side_effects = data.get("side effects", "Not found")
    side_effects = re.sub(r"\s+", " ", side_effects)
    effects_list = re.split(r",|\band\b", side_effects)
    effects_list = [effect.strip() for effect in effects_list if effect.strip()]
    side_effects = ", ".join(effects_list)
    
    return {
        "manufacturer": manufacturer,
        "ingredient": ingredient,
        "price": price,
        "dosage": dosage,
        "side_effects": side_effects
    }


def extract_retailer_info(raw_info: str) -> dict:
    """
    Extracts retailer metadata from the generic drug record.
    Expected format: variable key-value pairs that may include retailer and price info.
    """
    data = {}
    for field in raw_info.split(";"):
        if ":" in field:
            key, value = field.split(":", 1)
            data[key.strip().lower()] = value.strip()
    
    retailer = data.get("retailer", "Unknown")
    
    price_field = data.get("price", "Not found")
    m = re.search(r"(\$\d+\.\d+).*?(for\s+.+)", price_field, re.IGNORECASE)
    if m:
        price = m.group(1).strip()
        quantity = m.group(2).strip()
    else:
        price = price_field
        quantity = data.get("quantity", "")
    
    return {
        "retailer": retailer,
        "price": price,
        "quantity": quantity
    }


def normalize_drug_name(raw_name: str) -> str:
    """
    Uses Gemini to normalize the raw drug name.
    """
    prompt = f"Normalize the following drug name to its standard format as stored in our database: {raw_name}"
    payload = {"prompt": prompt, "max_tokens": 20}
    headers = {"Authorization": f"Bearer {GEMINI_API_KEY}", "Content-Type": "application/json"}
    
    try:
        response = requests.post(GEMINI_API_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        normalized_name = data.get("generated_text", "").strip()
        return normalized_name if normalized_name else raw_name
    except Exception as e:
        logger.error(f"Error normalizing drug name: {e}")
        return raw_name


def get_possible_ingredients(active_ing: str) -> list:
    """
    Generate variations of the active ingredient to increase chances of finding a match.
    """
    variations = [active_ing]
    
    # Add common variations
    if " " in active_ing:
        variations.append(active_ing.replace(" ", ""))
    
    # Add capitalized version
    variations.append(active_ing.capitalize())
    
    # Remove special characters
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', active_ing)
    if cleaned != active_ing:
        variations.append(cleaned)
    
    return list(set(variations))  # Remove duplicates


@app.route('/search', methods=['POST'])
def search():
    data = request.get_json()
    raw_brand = data.get("brand_drug", "").strip()
    if not raw_brand:
        return jsonify({"error": "No brand drug provided"}), 400

    # Normalize the drug name using Gemini.
    normalized_brand = normalize_drug_name(raw_brand)
    logger.info(f"Normalized drug name: {normalized_brand}")
    
    # Fetch brand record from "brand_drug" namespace (ID = normalized brand with spaces removed).
    brand_id = normalized_brand.replace(" ", "")
    logger.info(f"Fetching brand drug with ID: {brand_id}")
    brand_fetch = index.fetch(ids=[brand_id], namespace="brand_drug")
    
    if brand_id in brand_fetch.get('vectors', {}):
        brand_raw = brand_fetch['vectors'][brand_id]['metadata'].get("text", "No drug info available")
        logger.info(f"Found brand drug info: {brand_raw[:100]}...")  # Log first 100 chars
    else:
        logger.warning(f"No information found for brand drug: {brand_id}")
        brand_raw = "No information found for this drug"
    
    brand_data = extract_generic_info(brand_raw)
    brand_info_str = (
        f"Manufacturer: {brand_data['manufacturer']}\n"
        f"Ingredient: {brand_data['ingredient']}\n"
        f"Average retail price: {brand_data['price']} / {brand_data['dosage']}\n"
        f"Side Effects: {brand_data['side_effects']}"
    )
    
    # Get the generic alternatives based on the active ingredient.
    active_ing = brand_data.get("ingredient", "").lower()  # e.g., "ibuprofen"
    logger.info(f"Active ingredient extracted: {active_ing}")
    
    if active_ing == "not found" or not active_ing:
        logger.warning("No active ingredient found in brand drug data")
        return jsonify({
            "brand_drug": normalized_brand,
            "brand_info": brand_info_str,
            "generic_summary": "Generic alternative info not available",
            "retailer_info": []
        })
    
    # Try multiple possible variations of the ingredient name
    possible_ingredients = get_possible_ingredients(active_ing)
    logger.info(f"Trying possible ingredient variations: {possible_ingredients}")
    
    # Generate a combined generic summary from retailer data
    retailer_list = ["walgreens", "cvs", "walmart", "costco", "riteaid", "target"]
    retailer_info_list = []
    generic_info_found = False
    
    for ingredient_variant in possible_ingredients:
        for retailer in retailer_list:
            rec_id = f"{ingredient_variant}:{retailer}"
            logger.info(f"Attempting to fetch generic drug with ID: {rec_id}")
            
            try:
                rec_fetch = index.fetch(ids=[rec_id], namespace="generic_drug")
                
                if rec_id in rec_fetch.get('vectors', {}):
                    rec_raw = rec_fetch['vectors'][rec_id]['metadata'].get("text", "")
                    logger.info(f"Found generic drug info for {retailer}: {rec_raw[:100]}...")  # Log first 100 chars
                    
                    # Extract retailer info directly instead of using Gemini
                    retailer_data = extract_retailer_info(rec_raw)
                    formatted_info = f"Retailer: {retailer_data['retailer']}; Price: {retailer_data['price']} for {retailer_data['quantity']}"
                    retailer_info_list.append(formatted_info)
                    generic_info_found = True
            except Exception as e:
                logger.error(f"Error fetching generic drug data for {rec_id}: {e}")
    
    # Create a generic summary from the collected retailer data
    if generic_info_found:
        generic_summary_str = (
            f"Ingredient: {active_ing}\n"
            f"Available at: {', '.join(set([info.split(';')[0].replace('Retailer:', '').strip() for info in retailer_info_list if 'Retailer:' in info]))}\n"
            f"Price range: {min([info.split(';')[1].replace('Price:', '').strip().split(' for')[0] for info in retailer_info_list if 'Price:' in info] or ['N/A'])} - "
            f"{max([info.split(';')[1].replace('Price:', '').strip().split(' for')[0] for info in retailer_info_list if 'Price:' in info] or ['N/A'])}"
        )
    else:
        generic_summary_str = "Generic alternative info not available"
        logger.warning(f"No generic drug information found for active ingredient: {active_ing}")
    
    return jsonify({
        "brand_drug": normalized_brand,
        "brand_info": brand_info_str,
        "generic_summary": generic_summary_str,
        "retailer_info": retailer_info_list
    })


@app.route('/debug/list_records', methods=['GET'])
def list_records():
    """
    Debug endpoint to list available records in the database.
    Use with caution as this may expose sensitive data.
    """
    try:
        # Get query parameters
        namespace = request.args.get('namespace', 'brand_drug')
        limit = int(request.args.get('limit', 10))
        
        # List vectors in the namespace (will depend on Pinecone API capabilities)
        # Note: This is a simplified approach and might need adaptation based on Pinecone's API
        # In a real implementation, you would use pagination and proper filtering
        
        # This is just a placeholder - actual implementation depends on your Pinecone version
        # For newer Pinecone versions, you might need to use a query with a dummy vector
        result = {
            "message": f"Debug endpoint to list records in {namespace} namespace",
            "note": "This endpoint needs to be implemented based on your Pinecone version's API"
        }
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
    