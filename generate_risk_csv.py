import geopandas as gpd
import pandas as pd
import os
from shapely.geometry import Point

# Paths to your data files (adjust if needed)
ZIP_GEOJSON = "static/zipbound.geojson"
FLOOD_GEOJSON = "static/Flood_Control_District_Zones.geojson"
WILDFIRE_GEOJSON = "static/AlamedaCounty_HazardZones.geojson"
FAULT_SHP = "data/hazfaults2014_proj.shp"  # Fault shapefile

OUTPUT_CSV = "output/zip_risk_scores.csv"

# Alameda County ZIP codes
alameda_zips = [
    "94501", "94502", "94536", "94538", "94539", "94541", "94542", "94544", "94545",
    "94546", "94550", "94551", "94552", "94555", "94560", "94566", "94568", "94577",
    "94578", "94579", "94580", "94586", "94587", "94601", "94602", "94603", "94605",
    "94606", "94607", "94608", "94609", "94610", "94611", "94612", "94618", "94619",
    "94621", "94706"
]

def load_geodata(path):
    print(f"Loading {path} ...")
    gdf = gpd.read_file(path)
    print(f"Loaded {len(gdf)} features from {path}")
    return gdf

def main():
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

    # Load GeoDataFrames
    zip_gdf = load_geodata(ZIP_GEOJSON)
    flood_gdf = load_geodata(FLOOD_GEOJSON)
    wildfire_gdf = load_geodata(WILDFIRE_GEOJSON)
    fault_gdf = load_geodata(FAULT_SHP)

    # Standardize ZIP code column
    if 'ZCTA5CE10' in zip_gdf.columns:
        zip_gdf = zip_gdf.rename(columns={'ZCTA5CE10': 'ZIP'})
    elif 'ZIP' not in zip_gdf.columns:
        raise ValueError("ZIP code column not found in ZIP GeoJSON")
    zip_gdf = zip_gdf[['ZIP', 'geometry']]
    zip_gdf = zip_gdf[zip_gdf['ZIP'].isin(alameda_zips)].reset_index(drop=True)

    # --- FLOOD CONTROL DISTRICT ---
    print("Calculating flood district intersections...")
    flood_intersections = gpd.overlay(zip_gdf, flood_gdf, how='intersection')
    flood_intersections['intersect_area'] = flood_intersections.geometry.area
    idx = flood_intersections.groupby('ZIP')['intersect_area'].idxmax()
    dominant_flood = flood_intersections.loc[idx, ['ZIP', 'DIST_NAME', 'DISTRICT_ID']].copy()
    dominant_flood = dominant_flood.rename(columns={
        'DIST_NAME': 'Flood_Control_District',
        'DISTRICT_ID': 'Flood_Control_District_ID'
    })

    # --- WILDFIRE HAZARD ---
    hazard_rank_map = {
        "Non-Wildland/Non-Urban": 0,
        "Urban Unzoned": 1,
        "Low": 2,
        "Moderate": 3,
        "High": 4,
        "Very High": 5
    }
    if 'HAZ_CLASS' not in wildfire_gdf.columns:
        raise ValueError("HAZ_CLASS column not found in wildfire GeoJSON")

    wildfire_gdf['hazard_rank'] = wildfire_gdf['HAZ_CLASS'].map(hazard_rank_map).fillna(0).astype(int)

    wildfire_join = gpd.sjoin(zip_gdf, wildfire_gdf[['hazard_rank', 'geometry']], how='left', predicate='intersects')
    wildfire_max = wildfire_join.groupby('ZIP')['hazard_rank'].max().reset_index()
    inv_hazard_map = {v: k for k, v in hazard_rank_map.items()}
    wildfire_max['Wildfire_Hazard_Level'] = wildfire_max['hazard_rank'].map(inv_hazard_map)

    # --- EARTHQUAKE RISK based on centroid distance to fault lines ---
    print("Calculating earthquake risk based on fault proximity...")

    # Reproject to a projection in meters for distance calculation
    zip_gdf_m = zip_gdf.to_crs(epsg=3310)
    fault_gdf_m = fault_gdf.to_crs(epsg=3310)

    # Compute centroids in meter projection
    zip_gdf_m['centroid'] = zip_gdf_m.geometry.centroid
    fault_union = fault_gdf_m.unary_union

    # Compute distance from each ZIP centroid to nearest fault (in meters)
    def earthquake_risk(point):
        dist = point.distance(fault_union)  # in meters
        if dist < 500:
            return 10, "Very high earthquake risk due to proximity (<0.5 km) to active fault lines."
        elif dist < 1000:
            return 8, "High earthquake risk due to proximity (<1 km) to active fault lines."
        elif dist < 5000:
            return 5, "Moderate earthquake risk due to proximity (1–5 km) to active fault lines."
        elif dist < 10000:
            return 3, "Low earthquake risk due to moderate distance (5–10 km) from active fault lines."
        else:
            return 1, "Minimal earthquake risk due to distance >10 km from active fault lines."

    # Apply risk calculation
    zip_gdf_m[['Earthquake_Risk_Score', 'Earthquake_Risk_Explanation']] = zip_gdf_m['centroid'].apply(lambda pt: pd.Series(earthquake_risk(pt)))

    # Merge risk scores back into original (WGS84) ZIP GeoDataFrame
    zip_gdf[['Earthquake_Risk_Score', 'Earthquake_Risk_Explanation']] = zip_gdf_m[['Earthquake_Risk_Score', 'Earthquake_Risk_Explanation']]


    # --- MERGE ALL DATA ---
    print("Merging flood, wildfire, and earthquake data...")
    master_df = zip_gdf[['ZIP', 'Earthquake_Risk_Score', 'Earthquake_Risk_Explanation']].merge(
        dominant_flood, on='ZIP', how='left'
    ).merge(
        wildfire_max[['ZIP', 'Wildfire_Hazard_Level']], on='ZIP', how='left'
    )

    # Fill missing flood or wildfire data with default
    master_df['Flood_Control_District'] = master_df['Flood_Control_District'].fillna('UNKNOWN')
    master_df['Wildfire_Hazard_Level'] = master_df['Wildfire_Hazard_Level'].fillna('Unknown')
    master_df['Flood_Control_District_ID'] = master_df['Flood_Control_District_ID'].fillna('UNKNOWN')

    # --- FLOOD RISK MAP keyed by DISTRICT_ID with empty scores ---
    flood_risk_map_by_id = {
        142: {
            "score": 4,
            "explanation": "High flood risk due to steep hills causing rapid runoff, dense urban areas, and historic flooding along San Lorenzo Creek.",
            "chatbot_prompt": "Prepare for flooding risks including flash floods and urban runoff. Follow local evacuation routes and secure important belongings."
        },
        152: {
            "score": 3,
            "explanation": "Moderate to high risk from tidal influence combined with creek overflow in a heavily urbanized area.",
            "chatbot_prompt": "Expect tidal flooding and creek overflow. Stay informed of tide schedules and emergency alerts."
        },
        144: {
            "score": 7,
            "explanation": "Moderate risk from steep hillsides and flash flooding potential in parts of Oakland and Piedmont.",
            "chatbot_prompt": "Watch for flash floods in steep areas. Avoid low-lying regions during storms."
        },
        145: {
            "score": 3,
            "explanation": "Medium risk influenced by Alameda Creek’s flow and levee system; flooding possible in heavy storms.",
            "chatbot_prompt": "Flooding possible from Alameda Creek; have a plan for heavy storms and levee breaches."
        },
        146: {
            "score": 6,
            "explanation": "Moderate risk from bay-adjacent floodplains and increasing sea level rise impacts.",
            "chatbot_prompt": "Prepare for coastal flooding and sea level rise; monitor weather and tides."
        },
        147: {
            "score": 5,
            "explanation": "Lower risk suburban areas with flash flood potential from smaller creeks.",
            "chatbot_prompt": "Flash floods possible near smaller creeks; clear drainage and avoid flood-prone spots."
        },
        148: {
            "score": 8,
            "explanation": "Generally low risk due to more rural terrain and less urban development, but some large drainage areas carry water downstream rapidly.",
            "chatbot_prompt": "While overall flood risk is lower, large drainages may overflow. Prepare in advance during major storms."
        },
        149: {
            "score": 2,
            "explanation": "Generally low risk due to more rural terrain and less urban development.",
            "chatbot_prompt": "Low flood risk; standard precautions recommended."
        },
        153: {
            "score": 6,
            "explanation": "Moderate to high risk from urban flash flooding in narrow canyons and overwhelmed drainage.",
            "chatbot_prompt": "Urban flash flooding possible; avoid narrow canyons and keep drainage clear."
        },
        151: {
            "score": 5,
            "explanation": "Moderate risk from steep slopes, creek overflows, and occasional landslides.",
            "chatbot_prompt": "Be cautious of flooding, landslides, and creek overflow during storms."
        },
        "UNKNOWN": {
            "score": 0,
            "explanation": "Flood risk unknown.",
            "chatbot_prompt": "Flood risk data unavailable for your area; please stay alert to local weather reports."
        }
    }

    def get_flood_risk_info_by_id(district_id):
        try:
            key = int(district_id)
        except:
            key = "UNKNOWN"
        return flood_risk_map_by_id.get(key, flood_risk_map_by_id["UNKNOWN"])

    flood_info = master_df['Flood_Control_District_ID'].apply(get_flood_risk_info_by_id)
    master_df['Flood_Risk_Score'] = flood_info.apply(lambda x: x['score'])
    master_df['Flood_Risk_Explanation'] = flood_info.apply(lambda x: x['explanation'])
    master_df['Flood_Chatbot_Prompt'] = flood_info.apply(lambda x: x['chatbot_prompt'])

    # --- WILDFIRE RISK SCORE & CHATBOT PROMPT ---
    wildfire_risk_map = {
        "Non-Wildland/Non-Urban": {
            "score": 1,
            "chatbot_prompt": "Low wildfire risk; maintain defensible space and follow local fire safety guidelines."
        },
        "Urban Unzoned": {
            "score": 1,
            "chatbot_prompt": "Low wildfire risk; maintain defensible space and follow local fire safety guidelines."
        },
        "Low": {
            "score": 3,
            "chatbot_prompt": "Low to moderate wildfire risk; keep vegetation trimmed and prepare for fire season."
        },
        "Moderate": {
            "score": 5,
            "chatbot_prompt": "Moderate wildfire risk; prepare evacuation plans and emergency kits."
        },
        "High": {
            "score": 8,
            "chatbot_prompt": "High wildfire risk; stay alert during fire season and follow local advisories."
        },
        "Very High": {
            "score": 10,
            "chatbot_prompt": "Very high wildfire risk; implement all safety measures and evacuate promptly if advised."
        },
        "Unknown": {
            "score": 0,
            "chatbot_prompt": "Wildfire risk data unavailable; stay informed via local sources."
        }
    }

    wildfire_info = master_df['Wildfire_Hazard_Level'].apply(lambda lvl: wildfire_risk_map.get(lvl, wildfire_risk_map['Unknown']))
    master_df['Wildfire_Risk_Score'] = wildfire_info.apply(lambda x: x['score'])
    master_df['Wildfire_Chatbot_Prompt'] = wildfire_info.apply(lambda x: x['chatbot_prompt'])

    # --- Save CSV ---
    output_columns = [
        "ZIP",
        "Earthquake_Risk_Score",
        "Earthquake_Risk_Explanation",
        "Flood_Control_District",
        "Wildfire_Hazard_Level",
        "Flood_Risk_Score",
        "Flood_Risk_Explanation",
        "Flood_Chatbot_Prompt",
        "Wildfire_Risk_Score",
        "Wildfire_Chatbot_Prompt"
    ]

    master_df[output_columns].to_csv(OUTPUT_CSV, index=False)
    print(f"Saved {OUTPUT_CSV} with {len(master_df)} ZIP codes.")

if __name__ == "__main__":
    main()
