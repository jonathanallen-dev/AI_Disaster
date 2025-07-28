from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from geopy.geocoders import Nominatim
import requests
from openai import OpenAI
from dotenv import load_dotenv
import os, json
from shapely.geometry import Point
import pandas as pd
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import re
# --- Load env vars and setup ---
load_dotenv("secret.env")
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Load risk data from CSV ---
risk_df = pd.read_csv("output/zip_risk_scores.csv", dtype={"ZIP": str})
zip_risk_data = risk_df.set_index("ZIP").to_dict(orient="index")

# --- Geocoding fallback ---
def geocode_zip(zip_code):
    geolocator = Nominatim(user_agent="disaster_app")
    location = geolocator.geocode({"postalcode": zip_code, "country": "US"})
    return (location.latitude, location.longitude) if location else (37.75, -122.2)
def geocode_address(address_query):
    """
    Convert address to coordinates and ZIP code
    Returns: (lat, lon, zip_code, formatted_address) or None
    """
    geolocator = Nominatim(user_agent="disaster_app", timeout=10)
    
    try:
        # Check if input looks like coordinates (lat, lon)
        coord_pattern = r'^(-?\d+\.?\d*),\s*(-?\d+\.?\d*)(?:,.*)?$'
        coord_match = re.match(coord_pattern, address_query.strip())
        
        if coord_match:
            # Handle coordinate input with reverse geocoding
            lat, lon = float(coord_match.group(1)), float(coord_match.group(2))
            
            # Verify coordinates are in reasonable range for Bay Area
            if not (37.0 <= lat <= 38.5 and -123.0 <= lon <= -121.0):
                print(f"Coordinates {lat}, {lon} are outside Bay Area")
                return None
            
            # Reverse geocode to get address and ZIP
            location = geolocator.reverse((lat, lon), exactly_one=True)
            if location:
                zip_code = extract_zip_from_address(location.address)
                return (lat, lon, zip_code, location.address)
            else:
                return None
        
        # Handle regular address input
        clean_address = clean_address_input(address_query)
        
        # Try geocoding with different variations
        location = None
        
        # First try: exact input with Alameda County
        location = geolocator.geocode(clean_address + ", Alameda County, CA, USA")
        
        # Second try: without county specification
        if not location:
            location = geolocator.geocode(clean_address + ", CA, USA")
        
        # Third try: with Oakland area fallback
        if not location and "oakland" not in clean_address.lower():
            location = geolocator.geocode(clean_address + ", Oakland, CA, USA")
        
        # Fourth try: just the address as-is
        if not location:
            location = geolocator.geocode(clean_address)
            
        if location:
            # Verify location is in Alameda County area
            lat, lon = location.latitude, location.longitude
            if not (37.0 <= lat <= 38.5 and -123.0 <= lon <= -121.0):
                print(f"Address geocoded outside Bay Area: {lat}, {lon}")
                return None
            
            # Extract ZIP code from address components
            zip_code = extract_zip_from_address(location.address)
            
            return (
                location.latitude,
                location.longitude, 
                zip_code,
                location.address
            )
            
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print(f"Geocoding error: {e}")
    except ValueError as e:
        print(f"Coordinate parsing error: {e}")
        
    return None

def clean_address_input(address):
    """Clean and standardize address input"""
    # Remove extra spaces and normalize
    address = re.sub(r'\s+', ' ', address.strip())
    
    # Expand common abbreviations
    address = re.sub(r'\bst\b', 'street', address, flags=re.IGNORECASE)
    address = re.sub(r'\bave\b', 'avenue', address, flags=re.IGNORECASE)
    address = re.sub(r'\bblvd\b', 'boulevard', address, flags=re.IGNORECASE)
    address = re.sub(r'\bdr\b', 'drive', address, flags=re.IGNORECASE)
    address = re.sub(r'\brd\b', 'road', address, flags=re.IGNORECASE)
    address = re.sub(r'\bct\b', 'court', address, flags=re.IGNORECASE)
    address = re.sub(r'\bpl\b', 'place', address, flags=re.IGNORECASE)
    
    return address

def extract_zip_from_address(full_address):
    """Extract ZIP code from geocoded address"""
    # Look for 5-digit ZIP code pattern
    zip_match = re.search(r'\b(\d{5})\b', full_address)
    if zip_match:
        return zip_match.group(1)
    return None

# Enhanced form processing route (REMOVE THE DUPLICATE ROUTES)
@app.route("/form", methods=["POST"])
def process_form():
    """Process form with both ZIP and address search capabilities"""
    zip_code = request.form.get("zip_code", "").strip()
    address = request.form.get("address", "").strip()
    
    # Determine which search method to use
    if zip_code:
        # Direct ZIP code entry
        if not re.match(r'^\d{5}$', zip_code):
            # Return to form with error message
            return render_template("home.html", 
                                 error="Please enter a valid 5-digit ZIP code",
                                 form_data=request.form)
        
        # Verify ZIP is in our coverage area
        if zip_code not in zip_risk_data:
            return render_template("home.html",
                                 error=f"ZIP code {zip_code} is not in our coverage area (Alameda County)",
                                 form_data=request.form)
        
        final_zip = zip_code
        
    elif address:
        # Address search
        result = geocode_address(address)
        if not result:
            return render_template("home.html",
                                 error="Could not find address or address is outside our coverage area",
                                 suggestion="Please try a ZIP code instead or check that your address is in Alameda County, CA",
                                 form_data=request.form)
        
        lat, lon, final_zip, formatted_address = result
        
        if not final_zip:
            return render_template("home.html",
                                 error="Could not determine ZIP code from address",
                                 suggestion="Please try entering your ZIP code directly",
                                 form_data=request.form)
        
        # Verify ZIP is in our coverage area
        if final_zip not in zip_risk_data:
            return render_template("home.html",
                                 error=f"Address found but ZIP code {final_zip} is not in our coverage area",
                                 suggestion="This tool covers Alameda County, California",
                                 form_data=request.form)
            
    else:
        return render_template("home.html",
                             error="Please provide either a ZIP code or address",
                             form_data=request.form)
    
    # Validate other required fields
    household = request.form.get("household")
    preparedness = request.form.get("preparedness")
    
    if not household or not preparedness:
        return render_template("home.html",
                             error="Please fill in all required fields",
                             form_data=request.form)
    
    # Store form data in session
    session["zip_code"] = final_zip
    session["household"] = household
    session["special_needs"] = request.form.get("special_needs", "")
    session["preparedness"] = preparedness
    
    # Clear previous chat sessions
    for hazard in ["wildfire", "flood", "earthquake"]:
        session.pop(f"chat_{hazard}", None)
        session.pop(f"meta_{hazard}", None)
    
    return redirect(url_for("risk_summary"))

# Remove the enhanced_form route since we're consolidating into one route

# AP
# Error handling route for better user experience
@app.errorhandler(404)
def not_found_error(error):
    return render_template("home.html", 
                         error="Page not found. Please start with your location search."), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template("home.html", 
                         error="An internal error occurred. Please try again."), 500
@app.route("/search-address", methods=["POST"])
def search_address():
    """Handle address search and convert to ZIP code"""
    address_query = request.form.get("address", "").strip()
    
    if not address_query:
        return jsonify({"error": "Address is required"}), 400
    
    # Geocode the address
    result = geocode_address(address_query)
    
    if not result:
        return jsonify({
            "error": "Address not found or outside service area",
            "suggestion": "Please try a more specific address or use ZIP code search"
        }), 404
    
    lat, lon, zip_code, formatted_address = result
    
    # Check if ZIP code is in our coverage area (optional)
    if zip_code and zip_code not in zip_risk_data:
        return jsonify({
            "error": f"ZIP code {zip_code} is outside our coverage area",
            "found_address": formatted_address,
            "suggestion": "This tool covers Alameda County ZIP codes"
        }), 404
    
    return jsonify({
        "success": True,
        "zip_code": zip_code,
        "coordinates": [lat, lon],
        "formatted_address": formatted_address,
        "message": f"Found address in ZIP {zip_code}"
    })

# Enhanced form processing to handle both ZIP and address
@app.route("/form", methods=["POST"], endpoint='enhanced_form')
def process_enhanced_form():
    """Process form with both ZIP and address search capabilities"""
    zip_code = request.form.get("zip_code", "").strip()
    address = request.form.get("address", "").strip()
    
    # Determine which search method to use
    if zip_code:
        # Direct ZIP code entry
        if not re.match(r'^\d{5}$', zip_code):
            return jsonify({"error": "Please enter a valid 5-digit ZIP code"}), 400
        
        final_zip = zip_code
        
    elif address:
        # Address search
        result = geocode_address(address)
        if not result:
            return jsonify({
                "error": "Could not find address",
                "suggestion": "Please try a ZIP code instead"
            }), 404
        
        lat, lon, final_zip, formatted_address = result
        
        if not final_zip:
            return jsonify({
                "error": "Could not determine ZIP code from address"
            }), 404
            
    else:
        return jsonify({"error": "Please provide either a ZIP code or address"}), 400
    
    # Check if ZIP is in our data
    if final_zip not in zip_risk_data:
        return jsonify({
            "error": f"ZIP code {final_zip} is not in our coverage area",
            "coverage": "This tool covers Alameda County, California"
        }), 404
    
    # Store form data in session
    session["zip_code"] = final_zip
    session["household"] = request.form.get("household")
    session["special_needs"] = request.form.get("special_needs")
    session["preparedness"] = request.form.get("preparedness")
    
    # Clear previous chat sessions
    for hazard in ["wildfire", "flood", "earthquake"]:
        session.pop(f"chat_{hazard}", None)
        session.pop(f"meta_{hazard}", None)
    
    return redirect(url_for("risk_summary"))

# API endpoint for address suggestions (optional autocomplete)
@app.route("/api/address-suggestions")
def address_suggestions():
    """Simple address validation/suggestions"""
    query = request.args.get("q", "").strip()
    
    if len(query) < 3:
        return jsonify([])
    
    # Basic suggestions based on common Alameda County cities
    alameda_cities = [
        "Oakland", "Berkeley", "Fremont", "Hayward", "Alameda", 
        "San Leandro", "Union City", "Newark", "Dublin", "Pleasanton",
        "Livermore", "Castro Valley", "San Lorenzo", "Emeryville"
    ]
    
    suggestions = []
    for city in alameda_cities:
        if query.lower() in city.lower():
            suggestions.append(f"{query}, {city}, CA")
    
    return jsonify(suggestions[:5])
def get_risk_level(score):
    """Convert numeric risk score to text level"""
    if score >= 7:
        return "High"
    elif score >= 4:
        return "Moderate"
    else:
        return "Low"

def load_geojson_file(filename):
    """Helper function to load geojson files safely"""
    filepath = f"static/{filename}"
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error loading {filename}: {e}")
            return None
    else:
        print(f"File not found: {filename}")
        return None

def get_zip_boundary(zip_code):
    """Get ZIP boundary from zipbound.geojson"""
    zipbound_data = load_geojson_file("zipbound.geojson")
    if not zipbound_data:
        return None
    
    # Look for the specific ZIP code in the features
    for feature in zipbound_data.get('features', []):
        props = feature.get('properties', {})
        # Check various possible ZIP code field names
        zip_fields = ['ZCTA5CE10', 'ZIP', 'ZIPCODE', 'zip_code', 'ZIP_CODE']
        for field in zip_fields:
            if props.get(field) == zip_code:
                return {
                    "type": "FeatureCollection",
                    "features": [feature]
                }
    
    # If not found, create a fallback boundary
    lat, lon = geocode_zip(zip_code)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"ZIP": zip_code},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [lon - 0.02, lat - 0.02],
                        [lon + 0.02, lat - 0.02],
                        [lon + 0.02, lat + 0.02],
                        [lon - 0.02, lat + 0.02],
                        [lon - 0.02, lat - 0.02]
                    ]]
                }
            }
        ]
    }

# --- Home Page ---
@app.route("/")
def home():
    return render_template("home.html")

# --- Process Form (Updated) ---
@app.route("/form", methods=["POST"], endpoint='form')
def process_form():
    """Process form submission from home page"""
    session["zip_code"] = request.form.get("zip_code")
    session["household"] = request.form.get("household")
    session["special_needs"] = request.form.get("special_needs")
    session["preparedness"] = request.form.get("preparedness")

    # Clear previous chat sessions
    for hazard in ["wildfire", "flood", "earthquake"]:
        session.pop(f"chat_{hazard}", None)
        session.pop(f"meta_{hazard}", None)

    return redirect(url_for("risk_summary"))

# --- Optional: Redirect old form GET requests ---
@app.route("/form", methods=["GET"])
def redirect_form():
    """Redirect old form page requests to home"""
    return redirect(url_for("home"))

# --- Risk Summary Page ---
@app.route("/risk_summary")
def risk_summary():
    zip_code = session.get("zip_code")
    if not zip_code:
        return redirect(url_for("home"))

    data = zip_risk_data.get(zip_code)
    if not data:
        return f"Risk data for ZIP {zip_code} not found.", 404

    hazards = [
        ("Earthquake", data.get("Earthquake_Risk_Score", 0), data.get("Earthquake_Risk_Explanation", "")),
        ("Flood", data.get("Flood_Risk_Score", 0), data.get("Flood_Risk_Explanation", "")),
        ("Wildfire", data.get("Wildfire_Risk_Score", 0), data.get("Wildfire_Risk_Explanation", ""))
    ]
    hazards_sorted = sorted(hazards, key=lambda x: -float(x[1]))

    return render_template("risk_summary.html", zip_code=zip_code, hazards=hazards_sorted)

# --- Unified Hazard Map ---
@app.route("/unified_hazard_map")
def unified_hazard_map():
    """Unified hazard map showing all risks with toggleable layers"""
    zip_code = session.get("zip_code", "94601")
    
    # Get risk data for the ZIP code
    data = zip_risk_data.get(zip_code, {})
    
    # Prepare risk scores
    risk_scores = {
        'wildfire': {
            'score': data.get("Wildfire_Risk_Score", 0),
            'explanation': data.get("Wildfire_Risk_Explanation", "No data available")
        },
        'earthquake': {
            'score': data.get("Earthquake_Risk_Score", 0), 
            'explanation': data.get("Earthquake_Risk_Explanation", "No data available")
        },
        'flood': {
            'score': data.get("Flood_Risk_Score", 0),
            'explanation': data.get("Flood_Risk_Explanation", "No data available")
        }
    }
    
    return render_template("unified_hazard_map.html", 
                         zip_code=zip_code,
                         risk_scores=risk_scores)

# --- About Page ---
@app.route("/about")
def about():
    return render_template("about.html")

# --- Resources Page ---
@app.route("/resources")
def resources():
    return render_template("resources.html")

# --- API Endpoints ---

# Live Earthquake API
@app.route("/api/live-earthquakes")
def api_live_earthquakes():
    """API endpoint for live earthquake data with Alameda County filtering"""
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Alameda County bounds (more precise)
    bounds = {
        "min_lat": 37.4, "max_lat": 37.9,
        "min_lon": -122.4, "max_lon": -121.4
    }
    
    features = []
    for feature in data["features"]:
        coords = feature["geometry"]["coordinates"]
        lon, lat = coords[0], coords[1]
        
        if (bounds["min_lat"] <= lat <= bounds["max_lat"] and 
            bounds["min_lon"] <= lon <= bounds["max_lon"]):
            # Add some additional processing
            props = feature["properties"]
            props["depth"] = coords[2] if len(coords) > 2 else 0
            features.append(feature)
    
    return jsonify({"type": "FeatureCollection", "features": features})

# Wildfire zones API
@app.route("/api/wildfire-zones")
def api_wildfire_zones():
    """API endpoint for wildfire hazard zones"""
    data = load_geojson_file("FireHaz.geojson")
    if data:
        return jsonify(data)
    return jsonify({"error": "Wildfire zones data not found"}), 404

# Flood zones API
@app.route("/api/flood-zones")
def api_flood_zones():
    """API endpoint for flood hazard zones"""
    data = load_geojson_file("FldHaz.geojson")
    if data:
        return jsonify(data)
    return jsonify({"error": "Flood zones data not found"}), 404

# Fault lines API
@app.route("/api/fault-lines")
def api_fault_lines():
    """API endpoint for earthquake fault lines"""
    data = load_geojson_file("fault_lines.geojson")
    if data:
        return jsonify(data)
    return jsonify({"error": "Fault lines data not found"}), 404

# ZIP boundary API
@app.route("/api/zip-boundary/<zip_code>")
def api_zip_boundary(zip_code):
    """API endpoint for ZIP code boundary"""
    boundary_data = get_zip_boundary(zip_code)
    if boundary_data:
        return jsonify(boundary_data)
    return jsonify({"error": f"ZIP boundary for {zip_code} not found"}), 404

# County boundary API
@app.route("/api/county-boundary")
def api_county_boundary():
    """API endpoint for Alameda County boundary"""
    data = load_geojson_file("countbound.geojson")
    if data:
        return jsonify(data)
    return jsonify({"error": "County boundary data not found"}), 404

# Risk assessment API
@app.route("/api/risk-assessment/<zip_code>")
def api_risk_assessment(zip_code):
    """API endpoint for comprehensive risk assessment"""
    data = zip_risk_data.get(zip_code)
    if not data:
        return jsonify({"error": f"Risk data for ZIP {zip_code} not found"}), 404
    
    assessment = {
        "zip_code": zip_code,
        "risks": {
            "wildfire": {
                "score": float(data.get("Wildfire_Risk_Score", 0)),
                "level": get_risk_level(float(data.get("Wildfire_Risk_Score", 0))),
                "explanation": data.get("Wildfire_Risk_Explanation", ""),
                "hazard_level": data.get("Wildfire_Hazard_Level", "Unknown")
            },
            "earthquake": {
                "score": float(data.get("Earthquake_Risk_Score", 0)),
                "level": get_risk_level(float(data.get("Earthquake_Risk_Score", 0))),
                "explanation": data.get("Earthquake_Risk_Explanation", "")
            },
            "flood": {
                "score": float(data.get("Flood_Risk_Score", 0)),
                "level": get_risk_level(float(data.get("Flood_Risk_Score", 0))),
                "explanation": data.get("Flood_Risk_Explanation", ""),
                "control_district": data.get("Flood_Control_District", "Unknown")
            }
        },
        "overall_risk": max(
            float(data.get("Wildfire_Risk_Score", 0)),
            float(data.get("Earthquake_Risk_Score", 0)),
            float(data.get("Flood_Risk_Score", 0))
        )
    }
    
    return jsonify(assessment)

# --- Enhanced Shared Hazard Page Generator ---
def hazard_page(hazard, title, color):
    zip_code = session.get("zip_code", "94601")
    
    # Get ZIP boundary
    zip_geojson_data = get_zip_boundary(zip_code)
    zip_geojson = json.dumps(zip_geojson_data) if zip_geojson_data else "{}"

    chat_key = f"chat_{hazard}"
    meta_key = f"meta_{hazard}"
    
    # Get user inputs from session
    household_size = session.get("household", "Unknown")
    special_needs = session.get("special_needs", "None")
    preparedness_level = session.get("preparedness", "Unknown")

    inputs = (household_size, special_needs, preparedness_level)

    metadata = session.get(meta_key, {})
    chat = session.get(chat_key, [])

    regen_needed = (
        not metadata or
        metadata.get("zip_code") != zip_code or
        metadata.get("inputs") != inputs
    )

    if regen_needed:
        # Get comprehensive data for this ZIP code
        data = zip_risk_data.get(zip_code, {})
        
        # Extract risk scores and explanations
        earthquake_score = data.get("Earthquake_Risk_Score", "Unknown")
        earthquake_explanation = data.get("Earthquake_Risk_Explanation", "No data available")
        flood_score = data.get("Flood_Risk_Score", "Unknown")
        flood_explanation = data.get("Flood_Risk_Explanation", "No data available")
        wildfire_score = data.get("Wildfire_Risk_Score", "Unknown")
        wildfire_explanation = data.get("Wildfire_Risk_Explanation", "No data available")
        
        # Get specific hazard data
        if hazard == "wildfire":
            current_score = wildfire_score
            current_explanation = wildfire_explanation
            wildfire_hazard_level = data.get("Wildfire_Hazard_Level", "Unknown")
            custom_prompt = data.get("Wildfire_Chatbot_Prompt", "")
        elif hazard == "flood":
            current_score = flood_score
            current_explanation = flood_explanation
            flood_control_district = data.get("Flood_Control_District", "Unknown")
            custom_prompt = data.get("Flood_Chatbot_Prompt", "")
        elif hazard == "earthquake":
            current_score = earthquake_score
            current_explanation = earthquake_explanation
            custom_prompt = data.get("Earthquake_Chatbot_Prompt", "")
        
        # Build comprehensive context-aware prompt
        prompt_text = f"""You are a disaster preparedness assistant specializing in {hazard} safety for Alameda County residents. 

LOCATION CONTEXT:
- ZIP Code: {zip_code}
- {hazard.title()} Risk Score: {current_score}/10
- Risk Assessment: {current_explanation}
"""

        # Add hazard-specific context
        if hazard == "wildfire":
            prompt_text += f"- Wildfire Hazard Level: {wildfire_hazard_level}\n"
            prompt_text += f"- All Risk Scores - Wildfire: {wildfire_score}/10, Earthquake: {earthquake_score}/10, Flood: {flood_score}/10\n"
        elif hazard == "flood":
            prompt_text += f"- Flood Control District: {flood_control_district}\n"
            prompt_text += f"- All Risk Scores - Flood: {flood_score}/10, Wildfire: {wildfire_score}/10, Earthquake: {earthquake_score}/10\n"
        elif hazard == "earthquake":
            prompt_text += f"- All Risk Scores - Earthquake: {earthquake_score}/10, Wildfire: {wildfire_score}/10, Flood: {flood_score}/10\n"

        prompt_text += f"""
HOUSEHOLD CONTEXT:
- Household Size: {household_size} {"person" if household_size == "1" else "people"}
- Special Medical Needs: {special_needs if special_needs and special_needs.strip() else "None reported"}
- Current Preparedness Level: {preparedness_level}

INSTRUCTIONS:
1. Start with a personalized greeting that acknowledges their specific risk level and location
2. Reference their {hazard} risk score ({current_score}/10) and explain what this means for them specifically
3. Consider their household size, medical needs, and current preparedness level in all recommendations
4. If they have medical needs, prioritize those considerations in your advice
5. Give 4-6 specific, actionable steps tailored to their situation
6. Include local resources specific to Alameda County when relevant
7. If their risk score is high (7+), emphasize urgency and evacuation planning
8. If their risk score is moderate (4-6), focus on preparation and monitoring
9. If their risk score is low (1-3), focus on basic preparedness and awareness

CUSTOM GUIDANCE:
{custom_prompt if custom_prompt else f"Focus on {hazard}-specific safety measures appropriate for their risk level."}

Remember: Be encouraging but realistic about their risk level. Provide specific, actionable advice they can implement immediately."""

        messages = [
            {"role": "system", "content": f"You are a knowledgeable disaster preparedness assistant specializing in {hazard} safety for Alameda County. You provide personalized advice based on specific risk data, household needs, and local conditions. Always be helpful, encouraging, and specific in your recommendations."},
            {"role": "user", "content": prompt_text}
        ]

        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo", 
                messages=messages,
                max_tokens=800,  # Allow for longer, more detailed responses
                temperature=0.7  # Slight creativity while maintaining accuracy
            )
            initial_response = response.choices[0].message.content
        except Exception as e:
            initial_response = f"I apologize, but I'm having trouble accessing the AI system right now. However, based on your ZIP code {zip_code} and {hazard} risk score of {current_score}/10, here are some immediate steps you should take: [Error: {e}]"

        # Store comprehensive metadata for continued conversation
        metadata = {
            "zip_code": zip_code,
            "inputs": inputs,
            "initial_prompt": prompt_text,
            "initial_response": initial_response,
            "risk_data": {
                "current_score": current_score,
                "current_explanation": current_explanation,
                "all_scores": {
                    "earthquake": earthquake_score,
                    "flood": flood_score,
                    "wildfire": wildfire_score
                },
                "hazard_specific": data
            }
        }
        session[meta_key] = metadata
        session[chat_key] = []

    # Ensure initial response is in chat
    if not any(msg.get("content") == metadata["initial_response"] for msg in chat):
        chat.insert(0, {"role": "assistant", "content": metadata["initial_response"]})

    reply = None
    if request.method == "POST":
        user_input = request.form.get("message")
        if user_input:
            chat.append({"role": "user", "content": user_input})
            
            # Build context-aware conversation with all the risk data
            context_messages = [
                {"role": "system", "content": f"""You are a disaster preparedness assistant for {hazard} safety in Alameda County. 

CONTINUE THIS CONVERSATION WITH FULL CONTEXT:
- User Location: ZIP {zip_code}
- {hazard.title()} Risk: {metadata['risk_data']['current_score']}/10
- Risk Explanation: {metadata['risk_data']['current_explanation']}
- Household: {household_size} people
- Medical Needs: {special_needs if special_needs and special_needs.strip() else "None"}
- Preparedness Level: {preparedness_level}

Always reference their specific situation and risk level when answering questions. Be helpful and specific."""},
                {"role": "user", "content": metadata["initial_prompt"]},
                {"role": "assistant", "content": metadata["initial_response"]},
            ]
            
            # Add recent conversation history
            context_messages.extend(chat)

            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo", 
                    messages=context_messages,
                    max_tokens=600,
                    temperature=0.7
                )
                reply = response.choices[0].message.content
                chat.append({"role": "assistant", "content": reply})
                session[chat_key] = chat
            except Exception as e:
                reply = f"I'm sorry, I'm having trouble responding right now. Given your {hazard} risk level of {metadata['risk_data']['current_score']}/10 in ZIP {zip_code}, please refer to local emergency resources or try asking your question again. [Error: {e}]"

    # Load fault data for earthquake pages
    fault_geojson = None
    if hazard == "earthquake":
        fault_data = load_geojson_file("fault_lines.geojson")
        if fault_data:
            fault_geojson = json.dumps(fault_data)

    return render_template(
        f"{hazard}.html",
        zip_code=zip_code,
        zip_geojson=zip_geojson,
        initial_response=metadata["initial_response"],
        chat=chat,
        reply=reply,
        fault_geojson=fault_geojson if hazard == "earthquake" else None,
        # Pass risk data to template for display
        risk_score=metadata['risk_data']['current_score'],
        risk_explanation=metadata['risk_data']['current_explanation'],
        household_size=household_size,
        special_needs=special_needs,
        preparedness_level=preparedness_level
    )

# --- Hazard Routes ---
@app.route("/wildfire", methods=["GET", "POST"])
def wildfire():
    return hazard_page("wildfire", "Wildfire Risk", "#ff7043")

@app.route("/flood", methods=["GET", "POST"])
def flood():
    return hazard_page("flood", "Flood Risk", "#0288d1")

@app.route("/earthquake", methods=["GET", "POST"])
def earthquake():
    return hazard_page("earthquake", "Earthquake Risk", "#2196f3")

# --- Live Earthquake Map ---
@app.route("/live-earthquake-map")
def live_earthquake_map():
    zip_code = session.get("zip_code", "94601")
    zip_geojson_data = get_zip_boundary(zip_code)
    zip_geojson = json.dumps(zip_geojson_data) if zip_geojson_data else "{}"

    return render_template("live_earthquake_map.html", zip_geojson=zip_geojson)

# --- Run App ---
if __name__ == "__main__":
    app.run(debug=True)