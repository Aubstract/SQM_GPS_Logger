# program to generate a grid of gps points within a defined polygon on the map
# the points will be output in KML format for use with Google Earth

from math import cos, radians
from shapely.geometry import Point, Polygon
from pykml import parser

def main():
    # prompt user for path to polygon KML file
    polygon_kml_path = input("Enter the path to the polygon KML file: ")
    # read the polygon KML file
    with open(polygon_kml_path) as f:
        root = parser.parse(f).getroot()

    # Traverse to LinearRing
    linear_ring = root.Document.Placemark.Polygon.outerBoundaryIs.LinearRing

    # Extract coordinates as a string
    coords_text = linear_ring.coordinates.text.strip()

    # Parse into a list of tuples
    coords = []
    for line in coords_text.split():
        lon, lat, *_ = map(float, line.split(','))
        coords.append((lon, lat))

    print(coords)

    # Create a Polygon object
    polygon = Polygon(coords)

    # prompt user for grid size
    grid_size = float(input("Enter the grid size in meters: "))

    grid_lat = grid_size / 111320.0
    grid_lon = (2/3) * (grid_size / (111320.0 * abs(cos(radians(polygon.exterior.coords[0][0])))))

    one_meter_lat = 1 / 111320.0
    one_meter_lon = (2/3) * (1 / (111320.0 * abs(cos(radians(polygon.exterior.coords[0][0])))))

    # Generate grid points
    grid_points = []
    min_x, min_y, max_x, max_y = polygon.bounds
    x = min_x + one_meter_lon * 10
    while x <= max_x:
        y = min_y + one_meter_lat * 10
        while y <= max_y:
            point = Point(x, y)
            if polygon.contains(point):
                grid_points.append(point)
            y += grid_lat
        x += grid_lon

    # Print the number of grid points generated
    print(f"Number of grid points generated: {len(grid_points)}")

    # Output all grid_points in KML format
    kml_output = '<?xml version="1.0" encoding="UTF-8"?>\n'
    kml_output += '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
    kml_output += '<Document>\n'
    for i, point in enumerate(grid_points):
        kml_output += f'  <Placemark>\n'
        kml_output += f'    <name>{i}</name>\n'
        kml_output += f'    <Point>\n'
        kml_output += f'      <coordinates>{point.x},{point.y},0</coordinates>\n'
        kml_output += f'    </Point>\n'
        kml_output += f'  </Placemark>\n'
    kml_output += '</Document>\n'
    kml_output += '</kml>\n'

    # Write to output KML file to the same directory as the input file
    output_kml_path = polygon_kml_path.replace('.kml', '_grid.kml')
    with open(output_kml_path, 'w') as f:
        f.write(kml_output)

    print(f"Grid points saved to {output_kml_path}")

if __name__ == "__main__":
    main()


