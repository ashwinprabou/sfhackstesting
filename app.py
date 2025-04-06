from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
import re
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv
import logging
from functools import lru_cache
import concurrent.futures

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
# Move index creation to startup only, not every request
index = None

def initialize_pinecone():
    global index
    if index is None:
        indexes = pc.list_indexes()
        if INDEX_NAME not in indexes.names():
            pc.create_index(
                name=INDEX_NAME,
                dimension=1536,  # Ensure this matches your embedding dimension
                metric='euclidean',
                spec=ServerlessSpec(cloud='aws', region=PINECONE_ENV)
            )
        index = pc.Index(INDEX_NAME)
    return index

# Initialize at startup
initialize_pinecone()

# Gemini API configuration for normalization and formatting.
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Precompile regex patterns for better performance
PRICE_PATTERN = re.compile(r"(\$\d+\.\d+).*?(for\s+.+)", re.IGNORECASE)
EFFECTS_SPLIT_PATTERN = re.compile(r",|\band\b")
RETAILER_AVAILABLE_PATTERN = re.compile(r"available at\s+([\w\s]+?),", re.IGNORECASE)
RETAILER_IS_PATTERN = re.compile(r"([\w\s]+?)\s+is the retailer", re.IGNORECASE)
PRICE_DOLLAR_PATTERN = re.compile(r"\$(\d+\.?\d*)")
QUANTITY_PATTERN = re.compile(r"for\s+([\d\w\s\-]+)[\.,]", re.IGNORECASE)
USAGE_PATTERN = re.compile(r"(?:\band\s+)?(?:is\s+)?used to\s+([^\.]+)\.", re.IGNORECASE)
SIDE_EFFECTS_PATTERN = re.compile(r"(?:common\s+)?side effects include\s+([^\.]+)\.", re.IGNORECASE)

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
    m = PRICE_PATTERN.search(price_field)
    if m:
        price = m.group(1).strip()
        dosage = m.group(2).strip()
    else:
        price = price_field
        dosage = ""
    
    side_effects = data.get("side effects", "Not found")
    side_effects = re.sub(r"\s+", " ", side_effects)
    effects_list = EFFECTS_SPLIT_PATTERN.split(side_effects)
    effects_list = [effect.strip() for effect in effects_list if effect.strip()]
    side_effects = ", ".join(effects_list)
    
    # Extract usage/effects information
    usage = data.get("usage", data.get("uses", data.get("effects", "Not found")))
    
    return {
        "manufacturer": manufacturer,
        "ingredient": ingredient,
        "price": price,
        "dosage": dosage,
        "side_effects": side_effects,
        "usage": usage
    }


def extract_retailer_info(raw_info: str, retailer_name: str) -> dict:
    """
    Extracts retailer metadata from a generic drug record.
    """
    # Extract retailer using phrases "available at" or "is the retailer"
    retailer_match = RETAILER_AVAILABLE_PATTERN.search(raw_info)
    if not retailer_match:
        retailer_match = RETAILER_IS_PATTERN.search(raw_info)
    retailer = retailer_match.group(1).strip() if retailer_match else retailer_name.capitalize()

    # Extract the first dollar amount as the price.
    price_match = PRICE_DOLLAR_PATTERN.search(raw_info)
    price = f"${price_match.group(1)}" if price_match else "Price not available"

    # Extract quantity/dosage info from a phrase like "for 30 tablets"
    quantity_match = QUANTITY_PATTERN.search(raw_info)
    quantity = quantity_match.group(1).strip() if quantity_match else ""

    # For usage, search for "used to"
    usage_match = USAGE_PATTERN.search(raw_info)
    usage = usage_match.group(1).strip() if usage_match else "Usage not available"

    # For side effects
    side_effects_match = SIDE_EFFECTS_PATTERN.search(raw_info)
    side_effects = side_effects_match.group(1).strip() if side_effects_match else "Side effects not available"

    return {
        "retailer": retailer,
        "price": price,
        "quantity": quantity,
    }


@lru_cache(maxsize=128)
def normalize_drug_name(raw_name: str) -> str:
    """
    Uses Gemini to normalize the raw drug name with caching.
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


@lru_cache(maxsize=128)
def get_possible_ingredients(active_ing: str) -> tuple:
    """
    Generate variations of the active ingredient to increase chances of finding a match.
    Returns tuple for hashability with lru_cache.
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
    
    return tuple(set(variations))  # Remove duplicates and make hashable


def fetch_record(record_id, namespace):
    """Helper function to fetch a record, used for concurrent fetching"""
    try:
        fetch_result = index.fetch(ids=[record_id], namespace=namespace)
        if record_id in fetch_result.get('vectors', {}):
            return (record_id, fetch_result['vectors'][record_id]['metadata'].get("text", ""))
        return (record_id, None)
    except Exception as e:
        logger.error(f"Error fetching record {record_id}: {e}")
        return (record_id, None)


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
    
    try:
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
        
        # First try to find a generic summary record
        generic_info = None
        generic_records = {}
        
        # Prepare fetch tasks for concurrent execution
        fetch_tasks = []
        
        # Add generic summary fetch tasks
        for ingredient_variant in possible_ingredients:
            summary_id = f"{ingredient_variant}:generic"
            fetch_tasks.append((summary_id, "generic_drug"))
        
        # Add retailer-specific fetch tasks
        retailer_list = ["walgreens", "cvs", "walmart", "amazon", "costplus", "goodrx", "riteaid", "blink"]
        for ingredient_variant in possible_ingredients:
            for retailer in retailer_list:
                rec_id = f"{ingredient_variant}:{retailer}"
                fetch_tasks.append((rec_id, "generic_drug"))
        
        # Execute fetch tasks concurrently
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_record = {
                executor.submit(fetch_record, record_id, namespace): (record_id, namespace)
                for record_id, namespace in fetch_tasks
            }
            
            for future in concurrent.futures.as_completed(future_to_record):
                record_id, result_text = future.result()
                if result_text:
                    results[record_id] = result_text
        
        # Process generic summary results
        for ingredient_variant in possible_ingredients:
            summary_id = f"{ingredient_variant}:generic"
            if summary_id in results:
                generic_raw = results[summary_id]
                logger.info(f"Found generic summary: {generic_raw[:100]}...")
                generic_info = extract_generic_info(generic_raw)
                break
        
        # Process retailer results
        retailer_info_list = []
        retailer_records_found = False
        
        for ingredient_variant in possible_ingredients:
            for retailer in retailer_list:
                rec_id = f"{ingredient_variant}:{retailer}"
                if rec_id in results:
                    rec_raw = results[rec_id]
                    logger.info(f"Found generic drug info for {retailer}: {rec_raw[:100]}...")
                    
                    # Store the raw record
                    generic_records[retailer] = rec_raw
                    
                    # Extract retailer info directly
                    retailer_data = extract_retailer_info(rec_raw, retailer)
                    formatted_info = f"Retailer: {retailer_data['retailer']}; Price: {retailer_data['price']} for {retailer_data['quantity']}"
                    retailer_info_list.append(formatted_info)
                    retailer_records_found = True
                    
                    # If we don't have generic info yet, try to extract it from this record
                    if generic_info is None:
                        generic_info = extract_generic_info(rec_raw)
        
        # Create a generic summary focusing on effects/usage information
        if generic_info:
            generic_summary_str = (
                f"Ingredient: {active_ing}\n"
            )
        elif retailer_records_found:
            # If we have retailer records but no generic summary, try to extract info from the first retailer record
            first_retailer = next(iter(generic_records))
            temp_info = extract_generic_info(generic_records[first_retailer])
            generic_summary_str = (
                f"Manufacturer: Various manufacturers\n"
                f"Ingredient: {active_ing}\n"
                f"Usage: {temp_info.get('usage', 'Not specified')}\n"
                f"Side Effects: {temp_info.get('side_effects', 'Not specified')}"
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
    except Exception as e:
        logger.error(f"Error in search endpoint: {e}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500


@app.route('/debug/list_records', methods=['GET'])
def list_records():
    """
    Debug endpoint to list available records in the database.
    """
    try:
        # Get query parameters
        namespace = request.args.get('namespace', 'brand_drug')
        prefix = request.args.get('prefix', '')
        
        # For debugging - list some records that match a prefix if possible
        # This is a placeholder and depends on your Pinecone version's capabilities
        result = {
            "message": f"Debug endpoint to list records in {namespace} namespace",
            "search_prefix": prefix,
            "note": "Use this endpoint to explore what records exist in your database"
        }
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/debug/direct_fetch', methods=['GET'])
def direct_fetch():
    """
    Debug endpoint to directly fetch a specific record by ID.
    """
    try:
        record_id = request.args.get('id')
        namespace = request.args.get('namespace', 'generic_drug')
        
        if not record_id:
            return jsonify({"error": "No ID provided"}), 400
            
        fetch_result = index.fetch(ids=[record_id], namespace=namespace)
        
        if record_id in fetch_result.get('vectors', {}):
            record_data = fetch_result['vectors'][record_id]['metadata'].get("text", "No data available")
            return jsonify({
                "id": record_id,
                "namespace": namespace,
                "found": True,
                "data": record_data
            })
        else:
            return jsonify({
                "id": record_id,
                "namespace": namespace,
                "found": False,
                "message": "Record not found"
            })
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # Ensure Pinecone is initialized before starting the server
    initialize_pinecone()
    app.run(debug=True)
    