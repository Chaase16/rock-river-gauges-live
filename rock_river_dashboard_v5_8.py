from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

# Run the complete 5.7 dashboard, then add installable web-app metadata.
source_path = Path(__file__).with_name("rock_river_dashboard_v5_7.py")
source = source_path.read_text(encoding="utf-8")

# v5.7 itself patches the inherited v5.6/v5.4 source. Replacing the version
# token globally here makes the visible header/footer and API user-agent all
# report the actual deployed build number.
source = source.replace("5.7", "5.8")

exec(
    compile(source, str(source_path), "exec"),
    {"__name__": "__main__", "__file__": str(source_path)},
)

# Streamlit renders custom HTML in an iframe. This tiny component adds the
# manifest/theme/icon metadata to the parent document so mobile and desktop
# browsers can install Rock River Live like an app.
components.html(
    r'''
<script>
(function () {
  try {
    const d = window.parent.document;
    const head = d.head;

    function setMeta(name, content) {
      let el = head.querySelector('meta[name="' + name + '"]');
      if (!el) {
        el = d.createElement('meta');
        el.setAttribute('name', name);
        head.appendChild(el);
      }
      el.setAttribute('content', content);
    }

    function setLink(rel, href, extraAttrs) {
      let el = head.querySelector('link[rel="' + rel + '"]');
      if (!el) {
        el = d.createElement('link');
        el.setAttribute('rel', rel);
        head.appendChild(el);
      }
      el.setAttribute('href', href);
      if (extraAttrs) {
        Object.keys(extraAttrs).forEach(function (key) {
          el.setAttribute(key, extraAttrs[key]);
        });
      }
    }

    d.title = 'Rock River Live';
    setMeta('application-name', 'Rock River Live');
    setMeta('theme-color', '#0b4f6c');
    setMeta('apple-mobile-web-app-capable', 'yes');
    setMeta('apple-mobile-web-app-status-bar-style', 'default');
    setMeta('apple-mobile-web-app-title', 'Rock River Live');
    setLink('manifest', '/app/static/manifest.json', {type: 'application/manifest+json'});
    setLink('icon', '/app/static/rock-river-live.svg', {type: 'image/svg+xml'});
    setLink('apple-touch-icon', '/app/static/rock-river-live.svg');
  } catch (err) {
    console.debug('Rock River Live PWA metadata injection skipped:', err);
  }
})();
</script>
''',
    height=0,
    width=0,
)

with st.expander("📱 Install Rock River Live as an app"):
    st.markdown(
        "**iPhone / iPad:** open this page in Safari → tap **Share** → **Add to Home Screen** → leave **Open as Web App** turned on.\n\n"
        "**Android:** open in Chrome → tap the **⋮** menu → **Install app** or **Add to Home screen**.\n\n"
        "**Computer:** Chrome or Edge may show an **Install** icon in the address bar or an **Install Rock River Live** option in the browser menu."
    )

st.caption("Build 5.8 • installable Rock River Live web app")
