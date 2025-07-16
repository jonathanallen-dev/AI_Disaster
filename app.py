from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from geopy.geocoders import Nominatim
import os
import geopandas as gpd
import requests
from openai import OpenAI
from dotenv import load_dotenv
import json

# Load environment variables from secret.env
load_dotenv("secret.env")

app = Flask(__name__)
app.secret_key = "super-secret-key"

# --- OpenAI Setup ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Load ZIP shapes once ---
zip_gdf = gpd.read_file("static/ca_california_zip_codes_geo.min.json")

# --- Geocode fallback if ZIP not in shape file ---
def geocode_zip(zip_code):
    geolocator = Nominatim(user_agent="disaster_app")
    location = geolocator.geocode({"postalcode": zip_code, "country": "US"})
    return (location.latitude, location.longitude) if location else (37.75, -122.2)

# --- Home Page ---
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        session["zip_code"] = request.form.get("zip_code")
        session["household"] = request.form.get("household")
        session["special_needs"] = request.form.get("special_needs")
        session["preparedness"] = request.form.get("preparedness")

        # Clear any existing chat data to force regeneration
        for hazard in ["wildfire", "flood", "earthquake"]:
            session.pop(f"chat_{hazard}", None)
            session.pop(f"meta_{hazard}", None)

        return redirect(url_for("risk_summary"))
    return render_template("home.html")

# --- Risk Summary Page ---
@app.route("/summary")
def risk_summary():
    zip_code = session.get("zip_code")
    if not zip_code:
        return redirect(url_for("home"))

    top_risk = {
        "name": "Wildfire",
        "score": 7.2,
        "explanation": "This is a placeholder risk score. Add your model here."
    }

    return render_template("risk_summary.html", zip_code=zip_code, top_risk=top_risk)

# --- About Page ---
@app.route("/about")
def about():
    return render_template("about.html")

# --- Live Earthquakes in California (NEW) ---
@app.route("/live-earthquakes")
def live_earthquakes():
    usgs_url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    try:
        response = requests.get(usgs_url)
        data = response.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    california_bounds = {
        "min_lat": 32.0,
        "max_lat": 42.0,
        "min_lon": -125.0,
        "max_lon": -114.0
    }

    filtered_features = []
    for feature in data["features"]:
        lon, lat, *_ = feature["geometry"]["coordinates"]
        if (
            california_bounds["min_lat"] <= lat <= california_bounds["max_lat"]
            and california_bounds["min_lon"] <= lon <= california_bounds["max_lon"]
        ):
            filtered_features.append(feature)

    return jsonify({
        "type": "FeatureCollection",
        "features": filtered_features
    })

# --- Flood Data Route to fix GeometryCollection issue ---
@app.route("/flood_data")
def flood_data():
    path = os.path.join("static", "flood_zones.geojson")
    with open(path) as f:
        geojson_data = json.load(f)

    if geojson_data.get("type") == "GeometryCollection":
        features = []
        for geom in geojson_data.get("geometries", []):
            features.append({
                "type": "Feature",
                "properties": {},  # add properties here if available
                "geometry": geom
            })
        geojson_data = {
            "type": "FeatureCollection",
            "features": features
        }

    return jsonify(geojson_data)

# --- Shared Hazard Page Logic ---
def hazard_page(hazard, title, color):
    zip_code = session.get("zip_code", "94601")
    zip_shape = zip_gdf[zip_gdf["ZCTA5CE10"] == zip_code]

    if not zip_shape.empty:
        zip_geojson = zip_shape.to_json()
    else:
        lat, lon = geocode_zip(zip_code)
        from shapely.geometry import Point
        import geopandas as gpd
        point = Point(lon, lat)
        fallback = gpd.GeoDataFrame(index=[0], crs="EPSG:4326", geometry=[point.buffer(0.02)])
        zip_geojson = fallback.to_json()

    chat_key = f"chat_{hazard}"
    meta_key = f"meta_{hazard}"
    current_inputs = (
        session.get("household"),
        session.get("special_needs"),
        session.get("preparedness")
    )

    metadata = session.get(meta_key, {})
    chat = session.get(chat_key, [])

    regen_required = (
        not metadata or
        metadata.get("zip_code") != zip_code or
        metadata.get("inputs") != current_inputs
    )

    if regen_required:
        prompt = (
            f"You are a disaster preparedness assistant. Provide a customized, step-by-step safety plan for a household preparing for a {hazard} hazard.\n"
            f"- ZIP code: {zip_code}\n"
            f"- Household size: {current_inputs[0]}\n"
            f"- Special medical needs: {current_inputs[1] or 'None'}\n"
            f"- Taken preparedness steps: {current_inputs[2] or 'no'}\n\n"
            f"Tailor your response to this situation. Start with a friendly explanation of the risk and then give 3–5 specific actions this person should take."
        )
        messages = [
            {"role": "system", "content": "You are a helpful and concise disaster preparedness assistant."},
            {"role": "user", "content": prompt}
        ]

        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages
            )
            assistant_msg = response.choices[0].message.content
        except Exception as e:
            assistant_msg = f"Error generating AI response: {e}"

        metadata = {
            "zip_code": zip_code,
            "inputs": current_inputs,
            "initial_prompt": prompt,
            "initial_response": assistant_msg
        }
        session[meta_key] = metadata
        chat = []
        session[chat_key] = chat

    # Inject AI response if not already shown
    if not any(msg['content'] == metadata["initial_response"] for msg in chat):
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
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=full_messages
                )
                reply = response.choices[0].message.content
                chat.append({"role": "assistant", "content": reply})
                session[chat_key] = chat
            except Exception as e:
                reply = f"Error: {e}"

    return render_template(
        f"{hazard}.html",
        zip_code=zip_code,
        zip_geojson=zip_geojson,
        initial_response=metadata.get("initial_response"),
        chat=chat,
        reply=reply
    )

# --- Individual Hazard Pages ---
@app.route("/wildfire", methods=["GET", "POST"])
def wildfire():
    return hazard_page("wildfire", "Wildfire Risk", "#ff7043")

@app.route("/flood", methods=["GET", "POST"])
def flood():
    return hazard_page("flood", "Flood Risk", "#0288d1")

@app.route("/earthquake", methods=["GET", "POST"])
def earthquake():
    return hazard_page("earthquake", "Earthquake Risk", "#2196f3")

# --- Run Server ---
if __name__ == "__main__":
    app.run(debug=True)
