# Photogenic Sky - Home Assistant Integration

This custom integration for Home Assistant provides a "Photogenic Score" sensor to help photographers know when local weather conditions are ideal for taking pictures. The "score" is a percentage based on factors like cloud cover, visibility, and wind.

The entire integration is configured through the Home Assistant user interface. No YAML editing is required.



## Installation

### Recommended Method: HACS

This is the easiest and recommended way to install.

1.  Ensure you have [HACS (Home Assistant Community Store)](https://hacs.xyz/) installed.
2.  In HACS, go to `Integrations` > Click the three dots in the top right > `Custom repositories`.
3.  Add the URL to this GitHub repository (`https://github.com/YOUR_GITHUB_USERNAME/ha-photogenic-sky`) and select the `Integration` category.
4.  You can now find the "Photogenic Sky" integration in the HACS store. Click `Install`.
5.  Restart Home Assistant when prompted.

### Manual Method

1.  Navigate to the `custom_components` directory in your Home Assistant configuration folder.
2.  Copy the `photogenic_sky` directory from this repository into your `custom_components` directory.
3.  Restart Home Assistant.

## Configuration

1.  In Home Assistant, go to **Settings > Devices & Services**.
2.  Click the **+ ADD INTEGRATION** button in the bottom right.
3.  Search for **"Photogenic Sky"** and select it.
4.  A configuration dialog will appear. 
    -   Enter your **API Key** from WeatherAPI.com.
    -   Enter the **Location** you want to monitor (e.g., `London` or a zip/post code).
5.  Click **Submit**.

The integration will be set up, and you will have a new `sensor.photogenic_sky_your_location` entity ready to use!