Photogenic Sky - Home Assistant Integration
A smart weather sensor for photographers who care about the quality of light.

This is not just another weather integration. Photogenic Sky is designed from the ground up to tell you not just what the weather is, but what that weather means for your photography. It analyzes cloud layers, the sun's position, and atmospheric conditions to produce a "Photogenic Score" and a human-readable summary, helping you decide if it's the right time to grab your camera.

✨ Key Features
Multi-Location Support: Monitor conditions at home, your favorite hiking spot, or your next travel destination, all at the same time.

Cloud-Aware Scoring: Differentiates between high, wispy clouds (good for sunsets) and low, thick clouds (bad for light).

Time & Light Aware: Uses separate scoring models for Golden Hour, Blue Hour, Daytime, and Night.

Detailed Summary: Explains in plain English why conditions are good or bad.

Zero API Keys Required: Uses the free and open Open-Meteo API without the need for any complex setup.

🚀 Installation
The recommended way to install is through the Home Assistant Community Store (HACS).

Add this Repository to HACS:

Go to HACS > Integrations.

Click the three dots in the top-right corner and select Custom repositories.

Add the URL for this repository (https://github.com/jragarw/PhotogenicSky) and select the Integration category.

Install the Integration:

Search for "Photogenic Sky" in the HACS store and click Install.

Restart Home Assistant:

Restart your Home Assistant instance as prompted by HACS.

⚙️ Configuration
You can add multiple locations, and each one will be its own independent sensor.

In Home Assistant, go to Settings > Devices & Services.

Click the + ADD INTEGRATION button.

Search for "Photogenic Sky" and select it.

A dialog box will appear. Enter the name of the location you want to monitor (e.g., London, Yosemite National Park, or SW1A 0AA).

Click Submit. The integration will find the coordinates and create a new sensor.

Repeat for any other locations you wish to add.

📊 Dashboard Example
This integration provides a lot of data. The best way to see it all is with a standard Entities Card. This gives you a complete "at-a-glance" report for your chosen location.

<details>
<summary>Click to see the YAML for the recommended Entities Card</summary>

type: entities
title: Photogenic Report - Snowdonia
show_header_toggle: false
entities:
  - entity: sensor.photogenic_sky_snowdonia_national_park #<-- Change to your entity
    name: Photogenic Score

  - type: section
    label: Summary & Light

  - type: attribute
    entity: sensor.photogenic_sky_snowdonia_national_park
    attribute: photogenic_summary
    name: Summary
    icon: mdi:text-long

  - type: attribute
    entity: sensor.photogenic_sky_snowdonia_national_park
    attribute: lighting_condition
    name: Lighting
    icon: mdi:theme-light-dark

  - type: attribute
    entity: sensor.photogenic_sky_snowdonia_national_park
    attribute: sun_elevation
    name: Sun Elevation
    icon: mdi:sun-angle

  - type: section
    label: Cloud Conditions

  - type: attribute
    entity: sensor.photogenic_sky_snowdonia_national_park
    attribute: cloud_cover_high
    name: High Clouds
    icon: mdi:weather-cloudy-arrow-right

  - type: attribute
    entity: sensor.photogenic_sky_snowdonia_national_park
    attribute: cloud_cover_mid
    name: Mid Clouds
    icon: mdi:weather-partly-cloudy

  - type: attribute
    entity: sensor.photogenic_sky_snowdonia_national_park
    attribute: cloud_cover_low
    name: Low Clouds
    icon: mdi:weather-fog

  - type: section
    label: Planning Data

  - type: attribute
    entity: sensor.photogenic_sky_snowdonia_national_park
    attribute: sunrise
    name: Sunrise
    icon: mdi:weather-sunset-up

  - type: attribute
    entity: sensor.photogenic_sky_snowdonia_national_park
    attribute: sunset
    name: Sunset
    icon: mdi:weather-sunset-down
