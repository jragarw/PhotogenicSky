Photogenic Sky - Home Assistant Integration
A smart weather sensor for photographers who care about the quality of light.

This is not just another weather integration. Photogenic Sky is designed from the ground up to tell you not just what the weather is, but what that weather means for your photography. It analyzes cloud layers, the sun's position, and atmospheric conditions to produce a "Photogenic Score" and a human-readable summary, helping you decide if it's the right time to grab your camera.

✨ Why Use Photogenic Sky?
Standard weather reports can be misleading. 100% Cloud Cover could mean a beautiful sunset with high, wispy clouds, or it could mean a dull, gray day with a thick layer of low stratus clouds. This integration understands the difference.

Cloud-Aware Scoring: Differentiates between high, mid, and low cloud layers.

Time & Light Aware: Uses separate scoring models for Golden Hour, Blue Hour, Daytime, and Night.

Detailed Summary: Explains in plain English why conditions are good or bad.

Zero Configuration: No API keys required! It uses your Home Assistant's location automatically.

🚀 Installation
The recommended way to install is through the Home Assistant Community Store (HACS).

Add this Repository to HACS:

Go to HACS > Integrations.

Click the three dots in the top-right corner and select Custom repositories.

Add the URL for this repository and select the Integration category.

Install the Integration:

Search for "Photogenic Sky" in the HACS store.

Click Install.

Restart Home Assistant:

Restart your Home Assistant instance as prompted by HACS.

⚙️ Configuration
Configuration is handled entirely in the Home Assistant UI.

In Home Assistant, go to Settings > Devices & Services.

Click the + ADD INTEGRATION button.

Search for "Photogenic Sky" and select it.

You will see a confirmation dialog. Click Submit.

That's it! A new sensor.photogenic_sky_your_location_name entity will be created, ready to be added to your dashboards.

📊 Dashboard Examples
To get the most out of this integration, use a custom card like button-card to display the dynamic data.

<details>
<summary>Click to see the YAML for the recommended button-card</summary>

type: custom:button-card
entity: sensor.photogenic_sky_london #<-- Change to your entity
name: Photography Conditions
show_state: true
state_display: '[[[ return states[entity.entity_id].state + "%" ]]]'
icon: >
  [[[
    var condition = states[entity.entity_id].attributes.lighting_condition;
    if (condition == 'Golden Hour') return 'mdi:weather-sunset';
    if (condition == 'Blue Hour') return 'mdi:weather-night-partly-cloudy';
    if (condition == 'Night') return 'mdi:moon-waning-crescent';
    return 'mdi:weather-sunny';
  ]]]
styles:
  card:
    - padding: 12px
  state:
    - font-size: 28px
    - font-weight: bold
  name:
    - font-size: 16px
  label:
    - font-size: 14px
    - padding: 0 10px 10px 10px
    - white-space: normal
label: >
  [[[
    return states[entity.entity_id].attributes.photogenic_summary;
  ]]]
state:
  - value: 85
    operator: '>='
    color: '#4CAF50' # Green
  - value: 60
    operator: '>='
    color: '#FFC107' # Amber
  - value: 0
    operator: '>='
    color: '#F44336' # Red
