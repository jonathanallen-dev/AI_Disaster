from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from geopy.geocoders import Nominatim
import geopandas as gpd
import requests
from openai import OpenAI
from dotenv import load_dotenv
import os, json
from shapely.geometry import Point
import pandas as pd

# --- Load env vars and setup ---
load_dotenv("secret.env")
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Load shapefiles and data at startup ---
zip_gdf = gpd.read_file("static/ca_california_zip_codes_geo.min.json")
risk_df = pd.read_csv("output/zip_risk_scores.csv", dtype={"ZIP": str})
zip_risk_data = risk_df.set_index("ZIP").to_dict(orient="index")

# --- Geocoding fallback ---
def geocode_zip(zip_code):
    geolocator = Nominatim(user_agent="disaster_app")
    location = geolocator.geocode({"postalcode": zip_code, "country": "US"})
    return (location.latitude, location.longitude) if location else (37.75, -122.2)

# --- Home Page ---
@app.route("/")
def home():
    return render_template("home.html")

# --- Process Form (Updated) ---
@app.route("/form", methods=["POST"])
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

# --- About Page ---
@app.route("/about")
def about():
    return render_template("about.html")

# --- Resources Page ---
@app.route("/resources")
def resources():
    return render_template("resources.html")

# --- Earthquake API ---
@app.route("/live-earthquakes")
def live_earthquakes():
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    try:
        response = requests.get(url)
        data = response.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    bounds = {"min_lat": 32.0, "max_lat": 42.0, "min_lon": -125.0, "max_lon": -114.0}
    features = [
        f for f in data["features"]
        if bounds["min_lat"] <= f["geometry"]["coordinates"][1] <= bounds["max_lat"]
        and bounds["min_lon"] <= f["geometry"]["coordinates"][0] <= bounds["max_lon"]
    ]
    return jsonify({"type": "FeatureCollection", "features": features})

# --- Flood Data API ---
@app.route("/flood_data")
def flood_data():
    for path in [
        "static/Flood_Control_District_Zones.geojson",
        "static/flood_zones.geojson",
        "static/flood_control_zones.geojson"
    ]:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                break
            except Exception as e:
                print(f"Error reading {path}: {e}")
    else:
        return jsonify({"error": "Flood zones data not found"}), 404

    if data.get("type") == "GeometryCollection":
        data = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {}, "geometry": g} for g in data.get("geometries", [])]
        }

    return jsonify(data)

# --- Enhanced Shared Hazard Page Generator ---
def hazard_page(hazard, title, color):
    zip_code = session.get("zip_code", "94601")
    zip_shape = zip_gdf[zip_gdf["ZCTA5CE10"] == zip_code]

    if not zip_shape.empty:
        zip_geojson = zip_shape.to_json()
    else:
        lat, lon = geocode_zip(zip_code)
        fallback = gpd.GeoDataFrame(index=[0], crs="EPSG:4326", geometry=[Point(lon, lat).buffer(0.02)])
        zip_geojson = fallback.to_json()

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
        fault_path = os.path.join("static", "Fault_lines.Geojson")
        if os.path.exists(fault_path):
            try:
                with open(fault_path, "r") as f:
                    fault_geojson = json.load(f)
            except Exception as e:
                print(f"Error loading fault data: {e}")

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
    zip_shape = zip_gdf[zip_gdf["ZCTA5CE10"] == zip_code]

    if not zip_shape.empty:
        zip_geojson = zip_shape.to_json()
    else:
        lat, lon = geocode_zip(zip_code)
        fallback = gpd.GeoDataFrame(index=[0], crs="EPSG:4326", geometry=[Point(lon, lat).buffer(0.02)])
        zip_geojson = fallback.to_json()

    return render_template("live_earthquake_map.html", zip_geojson=zip_geojson)

# --- Run App ---
if __name__ == "__main__":
    app.run(debug=True)