from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import folium
from folium import plugins
import streamlit as st
import streamlit.components.v1 as components

# Run the validated 5.4 dashboard first on every Streamlit rerun.
# This preserves the live gauges, Lebanon hydrograph, flood-impact logic,
# East Branch/Theresa Marsh handling, password gate, and refresh behavior.
base_app_path = Path(__file__).with_name("rock_river_dashboard_v5_4.py")
base_source = base_app_path.read_text(encoding="utf-8")
base_source = base_source.replace('BUILD = "5.4"', 'BUILD = "5.6"', 1)
base_ns = {"__name__": "__main__", "__file__": str(base_app_path)}
exec(compile(base_source, str(base_app_path), "exec"), base_ns)

# ---------------------------------------------------------------------------
# Detailed interactive Rock River drainage-basin map
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🗺️ Interactive Rock River drainage basin")
st.caption(
    "Zoom, pan, switch basemaps, click subbasins, and click USGS gauge markers. "
    "The basin boundary layer uses the USGS Watershed Boundary Dataset (WBD)."
)

ROCK_HUC8S = {
    "07090001": "Upper Rock",
    "07090002": "Middle Rock",
    "07090003": "Pecatonica",
    "07090004": "Sugar",
    "07090005": "Lower Rock",
    "07090006": "Kishwaukee",
    "07090007": "Green",
}

WBD_QUERY_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/FeatureServer/4/query"
MONITORING_LOCATIONS_URL = (
    "https://api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations/items"
)


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_rock_huc8_geojson():
    hucs = ",".join(f"'{h}'" for h in ROCK_HUC8S)
    params = {
        "where": f"huc8 IN ({hucs})",
        "outFields": "huc8,name,states,areasqkm",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    url = WBD_QUERY_URL + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "RockRiverLive/5.6"})
    with urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_one_monitoring_location(site):
    params = {"id": f"USGS-{site}", "limit": 1, "f": "json"}
    url = MONITORING_LOCATIONS_URL + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "RockRiverLive/5.6"})
    with urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    features = payload.get("features", [])
    return features[0] if features else None


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_monitoring_locations(sites_tuple):
    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_one_monitoring_location, s): s for s in sites_tuple}
        for future in as_completed(futures):
            site = futures[future]
            try:
                feature = future.result()
                if feature:
                    results[site] = feature
            except Exception:
                pass
    return results


def safe_latest_value(latest_by_key, site, parameter):
    rec = latest_by_key.get((site, parameter))
    if not rec:
        return None
    try:
        return float(rec.get("value"))
    except Exception:
        return None


stations = base_ns.get("STATIONS", {})
latest_by_key = base_ns.get("latest_by_key", {})
param_stage = base_ns.get("PARAM_STAGE", "00065")
param_flow = base_ns.get("PARAM_FLOW", "00060")
station_link = base_ns.get("station_link")
historical_only = set(base_ns.get("HISTORICAL_ONLY_SITES", {}).keys())

# Build map with detailed topographic basemap plus optional street and imagery layers.
m = folium.Map(
    location=[42.35, -89.25],
    zoom_start=7,
    tiles=None,
    control_scale=True,
    prefer_canvas=True,
)

folium.TileLayer(
    tiles=(
        "https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
    ),
    attr="Esri, USGS, NOAA",
    name="Esri topographic",
    overlay=False,
    control=True,
    show=True,
).add_to(m)

folium.TileLayer(
    tiles="OpenStreetMap",
    name="OpenStreetMap",
    overlay=False,
    control=True,
    show=False,
).add_to(m)

folium.TileLayer(
    tiles=(
        "https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}"
    ),
    attr="Esri, Maxar, Earthstar Geographics",
    name="Satellite imagery",
    overlay=False,
    control=True,
    show=False,
).add_to(m)

# Seven official HUC8 subbasins that make up the Rock River basin.
basin_loaded = False
try:
    basin_geojson = fetch_rock_huc8_geojson()
    if basin_geojson.get("features"):
        basin_layer = folium.FeatureGroup(name="USGS Rock basin HUC8 boundaries", show=True)

        def basin_style(feature):
            huc = str(feature.get("properties", {}).get("huc8", ""))
            is_upper = huc == "07090001"
            return {
                "fillColor": "#2b83ba" if is_upper else "#74add1",
                "color": "#17365d",
                "weight": 4 if is_upper else 2,
                "fillOpacity": 0.18 if is_upper else 0.10,
            }

        folium.GeoJson(
            basin_geojson,
            name="Rock River HUC8 subbasins",
            style_function=basin_style,
            highlight_function=lambda feature: {
                "weight": 5,
                "fillOpacity": 0.28,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["name", "huc8", "states"],
                aliases=["Subbasin", "HUC8", "States"],
                sticky=True,
            ),
        ).add_to(basin_layer)
        basin_layer.add_to(m)
        basin_loaded = True
except Exception:
    basin_loaded = False

# Add all dashboard USGS stations with exact coordinates from the modern USGS API.
gauge_layer = folium.FeatureGroup(name="Dashboard USGS gauges", show=True)
active_sites = tuple(sorted(s for s in stations if s not in historical_only))
location_features = fetch_monitoring_locations(active_sites)

for site, name in stations.items():
    feature = location_features.get(site)
    if not feature:
        continue

    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        continue
    lon, lat = coords[0], coords[1]

    props = feature.get("properties") or {}
    official_name = props.get("monitoring_location_name") or name
    drainage_area = props.get("drainage_area")
    huc = props.get("hydrologic_unit_code") or ""

    stage = safe_latest_value(latest_by_key, site, param_stage)
    flow = safe_latest_value(latest_by_key, site, param_flow)
    stage_text = f"{stage:.2f} ft" if stage is not None else "—"
    flow_text = f"{flow:,.0f} CFS" if flow is not None else "—"
    drainage_text = (
        f"{float(drainage_area):,.0f} sq mi" if drainage_area not in (None, "") else "—"
    )

    gauge_url = station_link(site) if callable(station_link) else (
        f"https://waterdata.usgs.gov/monitoring-location/USGS-{site}/"
    )

    popup_html = f"""
    <div style='font-size:13px; line-height:1.45; min-width:240px'>
      <b>{name}</b><br>
      <span>{official_name}</span><br><br>
      <b>USGS:</b> {site}<br>
      <b>Stage:</b> {stage_text}<br>
      <b>Flow:</b> {flow_text}<br>
      <b>Drainage area:</b> {drainage_text}<br>
      <b>HUC:</b> {huc}<br><br>
      <a href='{gauge_url}' target='_blank'>Open USGS gauge</a>
    </div>
    """

    marker_color = "red" if site == "05424157" else "blue"
    folium.Marker(
        location=[lat, lon],
        tooltip=f"{name} • {stage_text} • {flow_text}",
        popup=folium.Popup(popup_html, max_width=330),
        icon=folium.Icon(color=marker_color, icon="tint", prefix="fa"),
    ).add_to(gauge_layer)

gauge_layer.add_to(m)

# Retired East Branch gauge is useful context, so show it separately when metadata is available.
try:
    east_feature = fetch_one_monitoring_location("05424000")
    if east_feature:
        coords = (east_feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) >= 2:
            folium.Marker(
                location=[coords[1], coords[0]],
                tooltip="East Branch Rock / Mayville — historical gauge",
                popup=folium.Popup(
                    "<b>East Branch Rock / Mayville</b><br>USGS 05424000<br>"
                    "Discontinued 10/01/2011<br>Historical context only.",
                    max_width=300,
                ),
                icon=folium.Icon(color="gray", icon="info-sign"),
            ).add_to(gauge_layer)
except Exception:
    pass

# Useful map controls for phone and desktop.
plugins.Fullscreen(position="topleft").add_to(m)
plugins.MeasureControl(
    position="topleft",
    primary_length_unit="miles",
    secondary_length_unit="kilometers",
).add_to(m)
plugins.MiniMap(toggle_display=True, position="bottomright").add_to(m)
plugins.MousePosition(
    position="bottomleft",
    separator=" | ",
    prefix="Lat/Lon:",
    num_digits=5,
).add_to(m)
folium.LayerControl(collapsed=False, position="topright").add_to(m)

# Keep view on the full Rock River basin.
m.fit_bounds([[40.95, -91.10], [43.80, -87.70]])

components.html(m.get_root().render(), height=720, scrolling=False)

if basin_loaded:
    st.caption(
        "Boundary source: USGS Watershed Boundary Dataset. The Rock basin is HUC 070900, "
        "made up of seven HUC8 subbasins: Upper Rock, Middle Rock, Pecatonica, Sugar, "
        "Lower Rock, Kishwaukee, and Green. Upper Rock is emphasized because that is the "
        "main focus of this dashboard."
    )
else:
    st.warning(
        "The interactive basemap loaded, but the USGS basin-boundary service did not respond. "
        "Refresh later to reload the HUC8 boundary overlay."
    )

map_link_cols = st.columns(3)
with map_link_cols[0]:
    st.link_button(
        "USGS Watershed Boundary Dataset",
        "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer",
        use_container_width=True,
    )
with map_link_cols[1]:
    st.link_button(
        "NWS Rock River Basin overview",
        "https://www.weather.gov/lot/hydrology_basins",
        use_container_width=True,
    )
with map_link_cols[2]:
    st.link_button(
        "Wisconsin DNR Upper Rock",
        "https://dnr.wisconsin.gov/topic/Watersheds/basins/uprock",
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Rainfall resources
# ---------------------------------------------------------------------------
st.subheader("🌧️ Rainfall reports")
st.caption(
    "Use these reports to see where rain actually fell across the Rock River watershed. "
    "CoCoRaHS values are volunteer rain-gauge observations; NOAA/NWS products provide "
    "official precipitation and storm-report resources."
)

NOAA_OBSERVED_PRECIP_URL = "https://water.noaa.gov/precip/"
COCORAHS_WI_DAILY_URL = "https://www.cocorahs.org/ViewData/StateDailyPrecipReports.aspx?state=WI"
NWS_MKX_PRECIP_MAPS_URL = "https://www.weather.gov/mkx/Climate_Maps"
NWS_MKX_STORM_REPORTS_URL = (
    "https://forecast.weather.gov/product.php?"
    "site=MKX&issuedby=MKX&product=LSR&format=CI&version=1&glossary=1"
)

rain_col1, rain_col2 = st.columns(2)
with rain_col1:
    st.link_button("NOAA observed precipitation map", NOAA_OBSERVED_PRECIP_URL, use_container_width=True)
with rain_col2:
    st.link_button("Wisconsin CoCoRaHS daily reports", COCORAHS_WI_DAILY_URL, use_container_width=True)

rain_col3, rain_col4 = st.columns(2)
with rain_col3:
    st.link_button("NWS Wisconsin precipitation maps", NWS_MKX_PRECIP_MAPS_URL, use_container_width=True)
with rain_col4:
    st.link_button("NWS Milwaukee/Sullivan storm reports", NWS_MKX_STORM_REPORTS_URL, use_container_width=True)

# ---------------------------------------------------------------------------
# Full Lebanon NOAA/NWS impact list
# ---------------------------------------------------------------------------
st.subheader("🌊 Flood Impacts 🅘 — Lebanon / Hwy MM")
st.caption(
    "Complete NOAA/NWS impact wording. NWS identifies 9.0 ft as bankfull and "
    "10.0 ft as flood stage for LEBW3."
)

FLOOD_IMPACTS = [
    {"Stage ft": 14.0, "Flood impact": "Water impacts many properties around the town. Wiley Rd, N Crawfish Rd, Red Wing Rd, Morningside Rd, Poplar Grove Rd, Davidson Rd, Harvey Rd, Indian Lane, Follette Rd, Highview Rd, and Monroe Rd are closed.", "Official marker": ""},
    {"Stage ft": 13.0, "Flood impact": "Wiley Rd, N Crawfish Rd, Red Wing Rd, Morningside Rd, Poplar Grove Rd, Davidson Rd, Harvey Rd, Indian Lane, Follette Rd, Highview Rd and Monroe Rd are closed.", "Official marker": ""},
    {"Stage ft": 11.5, "Flood impact": "Davidson Rd, Harvey Rd, Indian Lane, Follette Rd, Highview Rd, Upham Rd, and Monroe Rd are closed.", "Official marker": ""},
    {"Stage ft": 11.0, "Flood impact": "East end of Davidson Rd, Monroe Rd and Upham Rd are closed.", "Official marker": ""},
    {"Stage ft": 10.0, "Flood impact": "Water over North end of Monroe Rd and Upham Rd.", "Official marker": "Flood stage"},
    {"Stage ft": 9.0, "Flood impact": "Water over Monroe Rd.", "Official marker": "Bankfull"},
    {"Stage ft": 8.0, "Flood impact": "Water impacts parts of Monroe Rd.", "Official marker": ""},
]

st.dataframe(
    FLOOD_IMPACTS,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Stage ft": st.column_config.NumberColumn(format="%.1f"),
        "Flood impact": st.column_config.TextColumn(width="large"),
        "Official marker": st.column_config.TextColumn(width="small"),
    },
)

st.caption("Build 5.6 • detailed interactive basin map + build 5.4 live dashboard")
