# This program will take in a csv file with geotagged light pollution data,
# group the data points based on trigger_id, and generate a heatmap of the data.

# C:\Users\Ben\Documents\SQM\data\2025-07-23_21-48-34\2025-07-23_21-48-34.csv
# C:\Users\Ben\Documents\SQM\data\2025-07-25_23-27-36\2025-07-25_23-27-36.csv
# C:\Users\Ben\Documents\SQM\data\2025-07-26_23-17-36\2025-07-26_23-17-36.csv
# C:\Users\Ben\Documents\SQM\data\2025-07-27_22-30-41\2025-07-27_22-30-41.csv
# C:\Users\Ben\Documents\SQM\data\2025-07-28_23-43-26\2025-07-28_23-43-26.csv

# C:\Users\Ben\Documents\SQM\data\2025-07-23_21-48-34\2025-07-23_21-48-34.csv, C:\Users\Ben\Documents\SQM\data\2025-07-26_23-17-36\2025-07-26_23-17-36.csv, C:\Users\Ben\Documents\SQM\data\2025-07-27_22-30-41\2025-07-27_22-30-41.csv, C:\Users\Ben\Documents\SQM\data\2025-07-28_23-43-26\2025-07-28_23-43-26.csv

import cartopy.crs as ccrs
import cartopy.io.img_tiles as cimgt
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.interpolate import griddata
from scipy.spatial import Delaunay
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.ops import unary_union
from sklearn.cluster import DBSCAN


# === Constants ===

DATA_FILE_HEADER = ["trigger_id",
                    "time_utc",
                    "time_local",
                    "latitude",
                    "longitude",
                    "altitude",
                    "speed",
                    "satellites",
                    "gps_time",
                    "sqm_time",
                    "temperature",
                    "count",
                    "frequency",
                    "brightness"]

LATITUDE_INDEX = 3
LONGITUDE_INDEX = 4
BRIGHTNESS_INDEX = 11

# ---- read & combine ----
paths_str = input("Enter the paths to the CSV files (comma-separated): ")
paths = [Path(p.strip()) for p in paths_str.split(",") if p.strip()]

if not paths:
    raise ValueError("No CSV paths provided.")

dfs = []
prev_max = -1  # so the first file's min will be shifted by (0 - min) if you want; we won't shift first file.

for i, p in enumerate(paths):
    if not p.is_file():
        raise FileNotFoundError(f"The file {p} does not exist.")
    if p.suffix.lower() != ".csv":
        raise ValueError(f"The file {p} is not a CSV file.")

    df = pd.read_csv(p, header=0, names=DATA_FILE_HEADER)

    # ensure trigger_id is integer (coerce first in case)
    df["trigger_id"] = pd.to_numeric(df["trigger_id"], errors="coerce").astype(int)

    if i == 0:
        # first file: keep as-is
        dfs.append(df)
        prev_max = df["trigger_id"].max()
    else:
        # shift so that this file's min becomes prev_max + 1
        cur_min = df["trigger_id"].min()
        shift = (prev_max + 1) - cur_min
        df["trigger_id"] = df["trigger_id"] + shift
        dfs.append(df)
        prev_max = df["trigger_id"].max()

# Final combined DataFrame
data = pd.concat(dfs, ignore_index=True)

# === Convert Data to Appropriate Types ===

# Convert fields to appropriate data types
data["trigger_id"] = data["trigger_id"].astype(int)
data["time_utc"] = pd.to_datetime(data["time_utc"], errors='coerce')
data["time_local"] = pd.to_datetime(data["time_local"], errors='coerce')
data["latitude"] = pd.to_numeric(data["latitude"], errors='coerce')
data["longitude"] = pd.to_numeric(data["longitude"], errors='coerce')
data["altitude"] = pd.to_numeric(data["altitude"], errors='coerce')
data["speed"] = pd.to_numeric(data["speed"], errors='coerce')
data["satellites"] = pd.to_numeric(data["satellites"], errors='coerce')
# Skip gps_time and sqm_time for now

# For SQM data, remove the suffixes
data["temperature"] = pd.to_numeric(data["temperature"].str.replace('C', ''), errors='coerce')
data["count"] = pd.to_numeric(data["count"].str.replace('c', ''), errors='coerce')
data["frequency"] = pd.to_numeric(data["frequency"].str.replace("Hz", ""), errors='coerce')
data["brightness"] = pd.to_numeric(data["brightness"].str.replace('m', ''), errors='coerce')

# === Group Data by Trigger ID ===

grouped_data = data.groupby("trigger_id").agg({
    "time_utc": "first",
    "time_local": "first",
    "latitude": "mean",
    "longitude": "mean",
    "altitude": "mean",
    "speed": "mean",
    "satellites": "first",
    "temperature": "mean",
    "count": "sum",
    "frequency": "mean",
    "brightness": "mean"
}).reset_index()

# Round the values for clarity
grouped_data = grouped_data.round({
    "latitude": 8,
    "longitude": 8,
    "altitude": 1,
    "speed": 1,
    "satellites": 0,
    "temperature": 1,
    "count": 0,
    "frequency": 0,
    "brightness": 2
})

# === Group Spatially to Combine Nearby Measurements ===
RADIUS_M = 50.0                  # your 50 m threshold
EARTH_R = 6_371_000.0            # meters

# DBSCAN expects radians for the haversine metric
coords_rad = np.radians(grouped_data[["latitude", "longitude"]].to_numpy())

db = DBSCAN(
    eps=RADIUS_M / EARTH_R,
    min_samples=1,
    metric="haversine"
).fit(coords_rad)

grouped_data["spatial_id"] = db.labels_

# Aggregate each spatial cluster
spatial_grouped = grouped_data.groupby("spatial_id").agg({
    "time_utc": "first",
    "time_local": "first",
    "latitude": "mean",
    "longitude": "mean",
    "altitude": "mean",
    "speed": "mean",
    "satellites": "first",
    "temperature": "mean",
    "count": "sum",
    "frequency": "mean",
    "brightness": "mean"
}).reset_index(drop=False)

# If you want to preserve the rest of your code that relies on the original
# column order and the *_INDEX constants, just rename spatial_id to trigger_id,
# and put the columns back in the same order your code expects.
spatial_grouped = spatial_grouped.rename(columns={"spatial_id": "trigger_id"})
cols_order = [
    "trigger_id",
    "time_utc",
    "time_local",
    "latitude",
    "longitude",
    "altitude",
    "speed",
    "satellites",
    "temperature",
    "count",
    "frequency",
    "brightness",
]
spatial_grouped = spatial_grouped[cols_order]

# From here on, just use spatial_grouped instead of grouped_data
grouped_data = spatial_grouped
grouped_data_np = grouped_data.to_numpy()

# === Convert to Numpy Array ===
#
#grouped_data_np = grouped_data.to_numpy()

# === Create a Grid for Interpolation ===

x = np.linspace(grouped_data_np[:,LONGITUDE_INDEX].min(), grouped_data_np[:,LONGITUDE_INDEX].max(), 600)
y = np.linspace(grouped_data_np[:,LATITUDE_INDEX].min(), grouped_data_np[:,LATITUDE_INDEX].max(), 600)
x, y = np.meshgrid(x, y)

# === Interpolate Brightness Values onto the Grid ===

grid_brightness = griddata(
    (grouped_data_np[:,LONGITUDE_INDEX], grouped_data_np[:,LATITUDE_INDEX]),
    grouped_data_np[:,BRIGHTNESS_INDEX],
    (x, y),
    method='cubic') # 'linear', 'nearest', 'cubic'

# === Create Mask to Eliminate Long Spans ===

points = np.array(list(zip(grouped_data_np[:,LONGITUDE_INDEX], grouped_data_np[:,LATITUDE_INDEX])))

tri = Delaunay(points)

MAX_DEGREE_EDGES = 2000 / 111_111 # 800 meters in degrees

valid_polys = []

for simplex in tri.simplices:
    triangle = points[simplex]
    # Calculate the lengths of the edges
    a = np.linalg.norm(triangle[0] - triangle[1])
    b = np.linalg.norm(triangle[1] - triangle[2])
    c = np.linalg.norm(triangle[2] - triangle[0])

    if max(a, b, c) > MAX_DEGREE_EDGES:
        poly = Polygon(triangle)
        if poly.is_valid and poly.area > 0:
            valid_polys.append(poly)

custom_shape = unary_union(valid_polys)

# === Apply the Mask to the Grid ===

mask = np.ones_like(grid_brightness, dtype=bool)
for i in range(x.shape[0]):
    for j in range(x.shape[1]):
        if not custom_shape.contains(Point(x[i, j], y[i, j])):
            mask[i, j] = False


masked_brightness = np.ma.array(grid_brightness, mask=mask)


# === Plot Heatmap on Satellite Imagery ===

esri_img = cimgt.QuadtreeTiles()

fig = plt.figure(figsize=(17, 11))
ax = plt.axes(projection=esri_img.crs)
PADDING = 0.002
ax.set_extent((x.min() - PADDING, x.max() + PADDING, y.min() - PADDING, y.max() + PADDING), crs=ccrs.PlateCarree())

ax.add_image(esri_img, 16, zorder=0)

# 3) Now draw your heatmap ON TOP
COLOR_PADDING = 0
im = ax.imshow(
    masked_brightness,
    extent=(x.min(), x.max(), y.min(), y.max()),
    origin='lower',
    cmap= 'inferno_r', # 'prism_r', 'nipy_spectral_r', 'inferno_r'
    aspect='equal',
    alpha=0.5,                 # let satellite show through
    transform=ccrs.PlateCarree(), # Tell Cartopy to use PlateCarree coordinates
    vmin=grouped_data_np[:,BRIGHTNESS_INDEX].min() - COLOR_PADDING,
    vmax=grouped_data_np[:,BRIGHTNESS_INDEX].max() + COLOR_PADDING,
    #vmin=17,
    #vmax=22,
    zorder=1
)

# Plot data points
ax.scatter(
    grouped_data_np[:,LONGITUDE_INDEX],
    grouped_data_np[:,LATITUDE_INDEX],
    c='white',
    edgecolors='black',
    s=8,
    transform=ccrs.PlateCarree(),
    zorder=2
)

for lon, lat, bright in zip(grouped_data_np[:,LONGITUDE_INDEX],
                            grouped_data_np[:,LATITUDE_INDEX],
                            grouped_data_np[:,BRIGHTNESS_INDEX]):
    ax.text(lon,
            lat,
            f'{bright:.2f}',
            transform=ccrs.PlateCarree(),
            fontsize=6,
            ha='left',
            va='top',
            color='black',
            zorder=3)

plt.title('Heatmap of Light Pollution', fontsize=16)

cbar = plt.colorbar(im, ax=ax, pad=0.03)
cbar.set_label(f"Brightness (mag/arcsec$^{2}$)", fontsize=14)
cbar.ax.invert_yaxis()
cbar.solids.set_alpha(1)

gl = ax.gridlines(draw_labels=True,
                  crs=ccrs.PlateCarree(),
                  alpha=0.0,
                  linestyle='--')
gl.top_labels = False    # Remove top labels
gl.right_labels = False  # Remove right labels
gl.xlabel_style = {'size': 10}
gl.ylabel_style = {'size': 10}

plt.subplots_adjust(
    left=0.08,
    right=1.01,
    top=0.95,
    bottom=0.05
)

# === Save ===

heatmap_filename = paths[-1].with_name(
    f"{paths[-1].stem}_heatmap_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png"
)
plt.savefig(heatmap_filename, dpi=300)
print(f"Heatmap saved as {heatmap_filename}")
print(f"{len(data)} total data points processed.")
print(f"{len(grouped_data_np)} unique locations points plotted.")
plt.show()
