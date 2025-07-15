from flask import Flask, render_template, request, redirect, url_for, session
from geopy.geocoders import Nominatim
import folium
import os
import pandas as pd
import geopandas as gpd
from openai import OpenAI
from dotenv import load_dotenv

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

# --- Shared Hazard Page Logic ---
def hazard_page(hazard, title, color):
    zip_code = session.get("zip_code", "94601")
    zip_shape = zip_gdf[zip_gdf["ZCTA5CE10"] == zip_code]

    if not zip_shape.empty:
        centroid = zip_shape.geometry.centroid.iloc[0]
        m = folium.Map(location=[centroid.y, centroid.x], zoom_start=13, height="500px")
        folium.GeoJson(
            zip_shape.to_json(),
            name="ZIP Boundary",
            style_function=lambda x: {
                'color': color,
                'weight': 3,
                'fillOpacity': 0.1
            }
        ).add_to(m)
        folium.Marker(
            location=[centroid.y, centroid.x],
            popup=f"ZIP Code: {zip_code}"
        ).add_to(m)
    else:
        lat, lon = geocode_zip(zip_code)
        m = folium.Map(location=[lat, lon], zoom_start=12, height="500px")
        folium.Circle(
            location=[lat, lon],
            radius=1500,
            color=color,
            fill=True,
            fill_opacity=0.3,
            popup=f"ZIP Code: {zip_code}"
        ).add_to(m)

    map_html = m._repr_html_()

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
        map_html=map_html,
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
