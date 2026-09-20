from pathlib import Path

# Build 5.7 reuses the complete 5.6 dashboard, but patches the Rock River
# interactive map before execution. The USGS WBD FeatureServer call used by
# 5.6 intermittently failed from Render. The hydrowfs MapServer endpoint is a
# USGS-hosted WBD service that explicitly supports GeoJSON in EPSG:4326.
source_path = Path(__file__).with_name("rock_river_dashboard_v5_6.py")
source = source_path.read_text(encoding="utf-8")

# Show the correct build number in the inherited dashboard header/footer.
source = source.replace(
    "base_source.replace('BUILD = \"5.4\"', 'BUILD = \"5.6\"', 1)",
    "base_source.replace('BUILD = \"5.4\"', 'BUILD = \"5.7\"', 1)",
    1,
)
source = source.replace("RockRiverLive/5.6", "RockRiverLive/5.7")
source = source.replace(
    'st.caption("Build 5.6 • detailed interactive basin map + build 5.4 live dashboard")',
    'st.caption("Build 5.7 • detailed interactive basin map + build 5.4 live dashboard")',
    1,
)

# Use the USGS hydrowfs MapServer for the Rock-basin HUC8 GeoJSON polygons.
source = source.replace(
    'WBD_QUERY_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/FeatureServer/4/query"',
    'WBD_QUERY_URL = "https://hydrowfs.nationalmap.gov/arcgis/rest/services/wbd/MapServer/4/query"',
    1,
)

# Add much more watershed detail as optional browser-side WMS overlays.
# HUC8 stays on by default; HUC10 and HUC12 can be switched on as users zoom.
wms_layers = r'''
# Extra official USGS WBD detail layers. These are rendered in the browser,
# so the detailed watershed lines do not depend on the server-side GeoJSON fetch.
WBD_WMS_URL = "https://hydro.nationalmap.gov/arcgis/services/wbd/MapServer/WMSServer"

folium.raster_layers.WmsTileLayer(
    url=WBD_WMS_URL,
    layers="4",
    name="USGS HUC8 subbasins — detailed lines",
    fmt="image/png",
    transparent=True,
    overlay=True,
    control=True,
    show=False,
    attr="USGS Watershed Boundary Dataset",
).add_to(m)

folium.raster_layers.WmsTileLayer(
    url=WBD_WMS_URL,
    layers="5",
    name="USGS HUC10 watersheds",
    fmt="image/png",
    transparent=True,
    overlay=True,
    control=True,
    show=False,
    attr="USGS Watershed Boundary Dataset",
).add_to(m)

folium.raster_layers.WmsTileLayer(
    url=WBD_WMS_URL,
    layers="6",
    name="USGS HUC12 subwatersheds — most detail",
    fmt="image/png",
    transparent=True,
    overlay=True,
    control=True,
    show=False,
    attr="USGS Watershed Boundary Dataset",
).add_to(m)

'''

source = source.replace(
    'folium.LayerControl(collapsed=False, position="topright").add_to(m)',
    wms_layers + 'folium.LayerControl(collapsed=False, position="topright").add_to(m)',
    1,
)

# Make the map instructions explain the new detail layers.
source = source.replace(
    '"Zoom, pan, switch basemaps, click subbasins, and click USGS gauge markers. "\n    "The basin boundary layer uses the USGS Watershed Boundary Dataset (WBD)."',
    '"Zoom, pan, switch basemaps, click subbasins, and click USGS gauge markers. "\n    "Use the layer control to turn on HUC10 watersheds or HUC12 subwatersheds for much more detail. "\n    "All watershed boundaries use the USGS Watershed Boundary Dataset (WBD)."',
    1,
)

# If the highlighted Rock-only polygon request ever fails, the HUC8/HUC10/HUC12
# WMS layers still remain available instead of implying the whole map is broken.
source = source.replace(
    '"The interactive basemap loaded, but the USGS basin-boundary service did not respond. "\n        "Refresh later to reload the HUC8 boundary overlay."',
    '"The highlighted Rock-only HUC8 polygon overlay did not load on this request. "\n        "The official USGS HUC8/HUC10/HUC12 detail layers are still available in the map layer control."',
    1,
)

exec(
    compile(source, str(source_path), "exec"),
    {"__name__": "__main__", "__file__": str(source_path)},
)
