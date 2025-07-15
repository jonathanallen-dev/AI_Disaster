# train_model.py
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
import joblib

# Load your feature data
df = pd.read_csv("data/zip_features.csv")  # Must include 'zip' and target column

# Separate features and target
X = df.drop(columns=["zip", "risk_score"])  # replace 'risk_score' with your actual target column
y = df["risk_score"]

# Train model
model = RandomForestRegressor(n_estimators=100, random_state=42)
model.fit(X, y)

# Save model
joblib.dump(model, "model/random_forest_model.pkl")
print("✅ Model saved at: model/random_forest_model.pkl")
