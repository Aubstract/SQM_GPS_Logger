import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN

# Get the CSV file path from the user
csv_filename = input("Enter the path to the CSV file: ")

# Load data
df = pd.read_csv(csv_filename, header=None,
                 names=["lat", "lon", "alt", "temp", "datetime", "brightness"])

# Convert to numeric
df["brightness"] = pd.to_numeric(df["brightness"], errors="coerce")

# Convert lat/lon to radians for haversine
coords = df[["lat", "lon"]].to_numpy()
coords_rad = np.radians(coords)

# Set your distance threshold (e.g. 10 meters)
epsilon_meters = 20

# Convert epsilon to radians
epsilon = epsilon_meters / 6371000.0

# Run DBSCAN
db = DBSCAN(eps=epsilon, min_samples=1, algorithm='ball_tree', metric='haversine')
cluster_labels = db.fit_predict(coords_rad)

# Assign cluster labels to dataframe
df["cluster"] = cluster_labels

# Group by cluster, compute average lat, lon, brightness
grouped = df.groupby("cluster").agg({
    "lat": "mean",
    "lon": "mean",
    "brightness": "mean"
}).reset_index(drop=True)

# Optionally round for clarity
grouped = grouped.round({"lat": 8, "lon": 8, "brightness": 2})

# View results
print(grouped)

# Save to CSV
grouped_path = csv_filename.replace('.csv', '_grouped_by_proximity.csv')
grouped.to_csv(grouped_path, index=False)
