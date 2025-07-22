# Program to take in a .csv file, bin the data spatially, and generate a heatmap using scipy.stats.gaussian_kde

import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

# Prompt user for CSV file path
csv_filename = input("Enter the path to the CSV file: ")
# Load data from CSV
data = np.loadtxt(csv_filename, delimiter=',')
# Extract latitude and longitude
latitudes = data[:, 0]
longitudes = data[:, 1]
# Extract brightness values
brightness = data[:, 2]
# Create a grid for interpolation
grid_lat = np.linspace(latitudes.min(), latitudes.max(), 200) # 100 points in latitude
grid_lon = np.linspace(longitudes.min(), longitudes.max(), 200) # 100 points in longitude
grid_lon, grid_lat = np.meshgrid(grid_lon, grid_lat)
# Interpolate brightness values onto the grid
grid_brightness = griddata((latitudes, longitudes), brightness, (grid_lat, grid_lon), method='cubic')
# Create a heatmap
plt.figure(figsize=(10, 8))
plt.imshow(grid_brightness,
           extent=(longitudes.min(), longitudes.max(), latitudes.min(), latitudes.max()),
           origin='lower',
           cmap='inferno_r',
           aspect='auto',
           vmin=12.0,
           vmax=22.0)
plt.colorbar(label='Brightness')
plt.scatter(longitudes, latitudes, c='white', edgecolors='black', s=10)  # Overlay original data points
# Add labels to each data pointm, where the numeric brightness value is the label
for i, (lon, lat, bright) in enumerate(zip(longitudes, latitudes, brightness)):
    plt.text(lon, lat, f'{bright:.2f}', fontsize=8, ha='center', va='bottom', color='black')
plt.title('Heatmap of Light Pollution')
plt.xlabel('Longitude')
plt.ylabel('Latitude')

# Save the heatmap as an image
heatmap_filename = csv_filename.replace('.csv', '_heatmap.png')
plt.savefig(heatmap_filename, dpi=300)
plt.show()

print(f"Heatmap saved as: {heatmap_filename}")
