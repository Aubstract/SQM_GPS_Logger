# Program to take in a .csv file of light pollution data and generate a heatmap.

import csv

def csv_to_kml(csv_filename, kml_filename):
    # Start the KML file structure
    kml_header = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
"""
    kml_footer = """  </Document>
</kml>"""

    # Read the csv data
    with open(csv_filename, newline='') as csvfile:
        reader = csv.reader(csvfile)
        placemarks = ""
        for row in reader:
            latitude = row[0].strip()
            longitude = row[1].strip()
            # altitude = row[2].strip()
            brightness = row[2].strip()

            # Create a Placemark for each data point
            placemark = f"""    <Placemark>
      <name>{brightness}</name>
      <Point>
        <coordinates>{longitude},{latitude}</coordinates>
      </Point>
    </Placemark>
"""
            placemarks += placemark

    # Combine and write to the KML file
    with open(kml_filename, 'w') as kmlfile:
        kmlfile.write(kml_header + placemarks + kml_footer)

def main():
    # get the path to the CSV file from the user
    csv_filename = input("Enter the path to the CSV file: ")

    # generate the KML filename based on the CSV filename, replacing .csv with .kml and adding '_heatmap'
    kml_filename = csv_filename.replace('.csv', '_points.kml')

    # Convert the CSV to KML
    csv_to_kml(csv_filename, kml_filename)

    print(f"KML file generated: {kml_filename}")

if __name__ == '__main__':
    main()