import streamlit as st

# Keep the validated 5.4 dashboard intact, including the Lebanon hydrograph,
# USGS live data, East Branch/Theresa Marsh handling, and gauge tables.
import rock_river_dashboard_v5_4 as app

st.divider()
st.subheader("🌧️ Rainfall reports")
st.caption(
    "Use these links to see where rain actually fell across the Rock River watershed. "
    "CoCoRaHS reports are volunteer rain-gauge observations; NOAA/NWS links provide "
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
    st.link_button(
        "NOAA observed precipitation map",
        NOAA_OBSERVED_PRECIP_URL,
        use_container_width=True,
    )
with rain_col2:
    st.link_button(
        "Wisconsin CoCoRaHS daily reports",
        COCORAHS_WI_DAILY_URL,
        use_container_width=True,
    )

rain_col3, rain_col4 = st.columns(2)
with rain_col3:
    st.link_button(
        "NWS Wisconsin precipitation maps",
        NWS_MKX_PRECIP_MAPS_URL,
        use_container_width=True,
    )
with rain_col4:
    st.link_button(
        "NWS Milwaukee/Sullivan storm reports",
        NWS_MKX_STORM_REPORTS_URL,
        use_container_width=True,
    )

st.subheader("🌊 Flood Impacts 🅘 — Lebanon / Hwy MM")
st.caption(
    "Complete NOAA/NWS impact wording. NWS identifies 9.0 ft as bankfull and "
    "10.0 ft as flood stage for LEBW3."
)

FLOOD_IMPACTS = [
    {
        "Stage ft": 14.0,
        "Flood impact": (
            "Water impacts many properties around the town. Wiley Rd, N Crawfish Rd, "
            "Red Wing Rd, Morningside Rd, Poplar Grove Rd, Davidson Rd, Harvey Rd, "
            "Indian Lane, Follette Rd, Highview Rd, and Monroe Rd are closed."
        ),
        "Official marker": "",
    },
    {
        "Stage ft": 13.0,
        "Flood impact": (
            "Wiley Rd, N Crawfish Rd, Red Wing Rd, Morningside Rd, Poplar Grove Rd, "
            "Davidson Rd, Harvey Rd, Indian Lane, Follette Rd, Highview Rd and Monroe Rd "
            "are closed."
        ),
        "Official marker": "",
    },
    {
        "Stage ft": 11.5,
        "Flood impact": (
            "Davidson Rd, Harvey Rd, Indian Lane, Follette Rd, Highview Rd, Upham Rd, "
            "and Monroe Rd are closed."
        ),
        "Official marker": "",
    },
    {
        "Stage ft": 11.0,
        "Flood impact": "East end of Davidson Rd, Monroe Rd and Upham Rd are closed.",
        "Official marker": "",
    },
    {
        "Stage ft": 10.0,
        "Flood impact": "Water over North end of Monroe Rd and Upham Rd.",
        "Official marker": "Flood stage",
    },
    {
        "Stage ft": 9.0,
        "Flood impact": "Water over Monroe Rd.",
        "Official marker": "Bankfull",
    },
    {
        "Stage ft": 8.0,
        "Flood impact": "Water impacts parts of Monroe Rd.",
        "Official marker": "",
    },
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

st.caption("Build 5.5 additions are active on top of the validated build 5.4 dashboard.")
