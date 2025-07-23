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

# --- Home Page (NEW) ---
@app.route("/")
def home():
    return render_template("home.html")

# --- Old Homepage = Form Page ---
@app.route("/form", methods=["GET", "POST"])
def form():
    if request.method == "POST":
        session["zip_code"] = request.form.get("zip_code")
        session["household"] = request.form.get("household")
        session["special_needs"] = request.form.get("special_needs")
        session["preparedness"] = request.form.get("preparedness")

        for hazard in ["wildfire", "flood", "earthquake"]:
            session.pop(f"chat_{hazard}", None)
            session.pop(f"meta_{hazard}", None)

        return redirect(url_for("risk_summary"))

    return render_template("form.html")

# --- Risk Summary Page ---
@app.route("/risk_summary")
def risk_summary():
    zip_code = session.get("zip_code")
    if not zip_code:
        return redirect(url_for("form"))

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

# --- Shared Hazard Page Generator ---
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
    inputs = (session.get("household"), session.get("special_needs"), session.get("preparedness"))

    metadata = session.get(meta_key, {})
    chat = session.get(chat_key, [])

    regen_needed = (
        not metadata or
        metadata.get("zip_code") != zip_code or
        metadata.get("inputs") != inputs
    )

    if regen_needed:
        prompt_key_map = {
            "wildfire": "Wildfire_Chatbot_Prompt",
            "flood": "Flood_Chatbot_Prompt",
            "earthquake": "Earthquake_Chatbot_Prompt"
        }
        data = zip_risk_data.get(zip_code)
        prompt_text = data.get(prompt_key_map.get(hazard)) if data else None

        if not prompt_text:
            prompt_text = (
                f"You are a disaster preparedness assistant. Provide a customized, step-by-step safety plan for a household preparing for a {hazard} hazard.\n"
                f"- ZIP code: {zip_code}\n"
                f"- Household size: {inputs[0]}\n"
                f"- Special medical needs: {inputs[1] or 'None'}\n"
                f"- Taken preparedness steps: {inputs[2] or 'no'}\n\n"
                f"Tailor your response to this situation. Start with a friendly explanation of the risk and then give 3–5 specific actions this person should take."
            )

        messages = [
            {"role": "system", "content": "You are a helpful and concise disaster preparedness assistant."},
            {"role": "user", "content": prompt_text}
        ]

        try:
            response = client.chat.completions.create(model="gpt-3.5-turbo", messages=messages)
            initial_response = response.choices[0].message.content
        except Exception as e:
            initial_response = f"Error generating AI response: {e}"

        metadata = {
            "zip_code": zip_code,
            "inputs": inputs,
            "initial_prompt": prompt_text,
            "initial_response": initial_response
        }
        session[meta_key] = metadata
        session[chat_key] = []

    if not any(msg["content"] == metadata["initial_response"] for msg in chat):
        chat.insert(0, {"role": "assistant", "content": metadata["initial_response"]})

    reply = None
    if request.method == "POST":
        user_input = request.form.get("message")
        if user_input:
            chat.append({"role": "user", "content": user_input})
            full_messages = [
                {"role": "system", "content": "You are a helpful and concise disaster preparedness assistant."},
                {"role": "user", "content": metadata["initial_prompt"]},
                {"role": "assistant", "content": metadata["initial_response"]},
            ] + chat

            try:
                response = client.chat.completions.create(model="gpt-3.5-turbo", messages=full_messages)
                reply = response.choices[0].message.content
                chat.append({"role": "assistant", "content": reply})
                session[chat_key] = chat
            except Exception as e:
                reply = f"Error: {e}"

    fault_path = os.path.join("static", "Fault_lines.Geojson")
    fault_geojson = None
    if hazard == "earthquake" and os.path.exists(fault_path):
        with open(fault_path, "r") as f:
            fault_geojson = json.load(f)

    return render_template(
        f"{hazard}.html",
        zip_code=zip_code,
        zip_geojson=zip_geojson,
        initial_response=metadata["initial_response"],
        chat=chat,
        reply=reply,
        fault_geojson=fault_geojson if hazard == "earthquake" else None,
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

@app.route("/resources")
def resources():
    return render_template("resources.html")

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
