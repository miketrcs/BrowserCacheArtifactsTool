"""
chrome-artifacts — Streamlit GUI
"""
import datetime
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Make sure the package is importable when launched from repo root
sys.path.insert(0, str(Path(__file__).parent))

from chrome_artifacts.parsers import (
    detect_version, parse_history, parse_downloads,
    parse_cookies, parse_bookmarks,
)
from chrome_artifacts.output import export_sqlite
from chrome_artifacts.cache import scan_cache, default_cache_path

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title='Chrome Artifacts',
    page_icon='🔍',
    layout='wide',
    initial_sidebar_state='expanded',
)

DEFAULT_PROFILE = str(Path.home() / 'Library/Application Support/Google/Chrome/Default')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_dt(dt) -> str:
    if dt is None:
        return ''
    if isinstance(dt, datetime.datetime):
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    return str(dt)


def history_to_df(items) -> pd.DataFrame:
    return pd.DataFrame([{
        'Visited':    fmt_dt(i.visit_time),
        'Title':      i.title or '',
        'URL':        i.url or '',
        'Visits':     i.visit_count,
        'Typed':      i.typed_count,
        'Transition': i.transition_friendly or '',
        'Duration':   i.visit_duration or '',
        'Source':     str(i.visit_source or ''),
    } for i in items])


def downloads_to_df(items) -> pd.DataFrame:
    return pd.DataFrame([{
        'Started':    fmt_dt(i.start_time),
        'Ended':      fmt_dt(i.end_time),
        'URL':        i.url or '',
        'Saved To':   i.target_path or '',
        'Received':   i.received_bytes,
        'Total':      i.total_bytes,
        'State':      i.state_friendly or '',
        'Danger':     str(i.danger_type or ''),
        'Opened':     bool(i.opened),
    } for i in items])


def cookies_to_df(items) -> pd.DataFrame:
    return pd.DataFrame([{
        'Created':   fmt_dt(i.creation_utc),
        'Last Used': fmt_dt(i.last_access_utc),
        'Expires':   fmt_dt(i.expires_utc),
        'Host':      i.host_key or '',
        'Path':      i.path or '',
        'Name':      i.name or '',
        'Value':     i.value or '',
        'Secure':    bool(i.secure),
        'HttpOnly':  bool(i.httponly),
        'Persistent':bool(i.persistent) if i.persistent is not None else False,
    } for i in items])


def bookmarks_to_df(items) -> pd.DataFrame:
    from chrome_artifacts.artifacts import BookmarkItem
    return pd.DataFrame([{
        'Added':   fmt_dt(i.date_added),
        'Name':    i.name or '',
        'URL':     getattr(i, 'url', ''),
        'Folder':  i.parent_folder or '',
        'Type':    i.row_type,
    } for i in items])


# ---------------------------------------------------------------------------
# Sidebar — profile config
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title('🔍 Chrome Artifacts')
    st.caption('macOS Chrome artifact browser')
    st.divider()

    profile_path = st.text_input(
        'Profile directory',
        value=DEFAULT_PROFILE,
        help='Path to the Chrome profile folder (usually "Default")',
    )

    decrypt = st.toggle(
        'Decrypt cookies',
        value=False,
        help='Retrieve encryption key from macOS Keychain to decrypt cookie values',
    )

    no_copy = st.toggle(
        'No file copy',
        value=False,
        help='Read DB files directly — faster, but may fail if Chrome is open',
    )

    st.divider()
    load_btn = st.button('Load Profile', type='primary', use_container_width=True)

    st.divider()
    st.caption('Export')
    export_name = st.text_input('Filename', value='chrome_artifacts.db')
    export_btn = st.button('Export to SQLite', use_container_width=True)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for key in ('history', 'downloads', 'cookies', 'bookmarks', 'images', 'version', 'loaded'):
    if key not in st.session_state:
        st.session_state[key] = None

if 'loaded' not in st.session_state:
    st.session_state.loaded = False


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
if load_btn:
    profile = str(Path(profile_path).expanduser().resolve())

    if not Path(profile).is_dir():
        st.error(f'Directory not found: `{profile}`')
    else:
        with st.spinner('Detecting Chrome version…'):
            version = detect_version(profile, no_copy=no_copy)
        st.session_state.version = version

        decryptor = None
        if decrypt:
            try:
                from chrome_artifacts.decrypt import MacDecryptor
                decryptor = MacDecryptor()
            except Exception as e:
                st.warning(f'Could not initialise decryptor: {e}')

        with st.spinner('Parsing history…'):
            st.session_state.history = parse_history(profile, version, no_copy=no_copy)
        with st.spinner('Parsing downloads…'):
            st.session_state.downloads = parse_downloads(profile, version, no_copy=no_copy)
        with st.spinner('Parsing cookies…'):
            st.session_state.cookies = parse_cookies(profile, version,
                                                      decryptor=decryptor, no_copy=no_copy)
        with st.spinner('Parsing bookmarks…'):
            st.session_state.bookmarks = parse_bookmarks(profile, version)

        with st.spinner('Scanning cache for images… (this may take a moment)'):
            cache_dir = default_cache_path(profile)
            st.session_state.images = scan_cache(cache_dir, max_results=500)

        st.session_state.loaded = True
        st.success('Profile loaded.')


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
if export_btn:
    if not st.session_state.loaded:
        st.sidebar.warning('Load a profile first.')
    else:
        out_path = str(Path(export_name).expanduser().resolve())
        export_sqlite(
            out_path,
            history=st.session_state.history,
            downloads=st.session_state.downloads,
            cookies=st.session_state.cookies,
            bookmarks=st.session_state.bookmarks,
        )
        st.sidebar.success(f'Saved to `{out_path}`')


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
if not st.session_state.loaded:
    st.markdown('## Chrome Artifact Browser')
    st.markdown(
        'Enter your Chrome profile path in the sidebar and click **Load Profile**.\n\n'
        '**Default location on macOS:**\n'
        '```\n~/Library/Application Support/Google/Chrome/Default\n```'
    )
    st.info('Tip: close Chrome before loading to ensure all databases are accessible.')
    st.stop()

# Summary metrics
v = st.session_state.version
st.caption(f'Chrome version range: {v[0]}–{v[-1]}  ·  Profile: `{profile_path}`')

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('History',   f'{len(st.session_state.history):,}')
c2.metric('Downloads', f'{len(st.session_state.downloads):,}')
c3.metric('Cookies',   f'{len(st.session_state.cookies):,}')
c4.metric('Bookmarks', f'{len(st.session_state.bookmarks):,}')
c5.metric('Cached Images', f'{len(st.session_state.images):,}')

st.divider()

tab_hist, tab_dl, tab_cook, tab_bm, tab_img = st.tabs(
    ['📄 History', '⬇️ Downloads', '🍪 Cookies', '🔖 Bookmarks', '🖼️ Cached Images']
)

# ---- History ---------------------------------------------------------------
with tab_hist:
    items = st.session_state.history
    df = history_to_df(items)

    col1, col2 = st.columns([3, 1])
    with col1:
        search = st.text_input('Search URL or title', key='hist_search', placeholder='Filter…')
    with col2:
        transitions = ['All'] + sorted(df['Transition'].unique().tolist())
        trans_filter = st.selectbox('Transition', transitions, key='hist_trans')

    if search:
        mask = (df['URL'].str.contains(search, case=False, na=False) |
                df['Title'].str.contains(search, case=False, na=False))
        df = df[mask]
    if trans_filter != 'All':
        df = df[df['Transition'] == trans_filter]

    st.caption(f'{len(df):,} records')
    st.dataframe(df, use_container_width=True, height=500,
                 column_config={
                     'URL': st.column_config.LinkColumn('URL'),
                     'Visits': st.column_config.NumberColumn('Visits', format='%d'),
                 })

# ---- Downloads -------------------------------------------------------------
with tab_dl:
    items = st.session_state.downloads
    df = downloads_to_df(items)

    col1, col2 = st.columns([3, 1])
    with col1:
        search = st.text_input('Search URL or path', key='dl_search', placeholder='Filter…')
    with col2:
        states = ['All'] + sorted(df['State'].unique().tolist())
        state_filter = st.selectbox('State', states, key='dl_state')

    if search:
        mask = (df['URL'].str.contains(search, case=False, na=False) |
                df['Saved To'].str.contains(search, case=False, na=False))
        df = df[mask]
    if state_filter != 'All':
        df = df[df['State'] == state_filter]

    st.caption(f'{len(df):,} records')
    st.dataframe(df, use_container_width=True, height=500,
                 column_config={
                     'URL': st.column_config.LinkColumn('URL'),
                     'Received': st.column_config.NumberColumn('Received', format='%d bytes'),
                     'Total':    st.column_config.NumberColumn('Total',    format='%d bytes'),
                 })

# ---- Cookies ---------------------------------------------------------------
with tab_cook:
    items = st.session_state.cookies
    df = cookies_to_df(items)

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        search = st.text_input('Search host or name', key='cook_search', placeholder='Filter…')
    with col2:
        secure_only = st.checkbox('Secure only', key='cook_secure')
    with col3:
        persistent_only = st.checkbox('Persistent only', key='cook_persist')

    if search:
        mask = (df['Host'].str.contains(search, case=False, na=False) |
                df['Name'].str.contains(search, case=False, na=False))
        df = df[mask]
    if secure_only:
        df = df[df['Secure']]
    if persistent_only:
        df = df[df['Persistent']]

    st.caption(f'{len(df):,} records')
    st.dataframe(df, use_container_width=True, height=500)

# ---- Bookmarks -------------------------------------------------------------
with tab_bm:
    items = st.session_state.bookmarks
    df = bookmarks_to_df(items)
    url_df = df[df['Type'] == 'bookmark']

    col1, col2 = st.columns([3, 1])
    with col1:
        search = st.text_input('Search name or URL', key='bm_search', placeholder='Filter…')
    with col2:
        folders = ['All'] + sorted(url_df['Folder'].unique().tolist())
        folder_filter = st.selectbox('Folder', folders, key='bm_folder')

    filtered = url_df.copy()
    if search:
        mask = (filtered['Name'].str.contains(search, case=False, na=False) |
                filtered['URL'].str.contains(search, case=False, na=False))
        filtered = filtered[mask]
    if folder_filter != 'All':
        filtered = filtered[filtered['Folder'] == folder_filter]

    st.caption(f'{len(filtered):,} bookmarks')
    st.dataframe(filtered.drop(columns=['Type']), use_container_width=True, height=500,
                 column_config={
                     'URL': st.column_config.LinkColumn('URL'),
                 })

# ---- Cached Images ---------------------------------------------------------
with tab_img:
    images = st.session_state.images

    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
    with col1:
        img_search = st.text_input('Filter by URL', key='img_search', placeholder='e.g. amazon, youtube…')
    with col2:
        mime_types = ['All'] + sorted({i.mime_type for i in images})
        mime_filter = st.selectbox('Type', mime_types, key='img_mime')
    with col3:
        min_w = st.number_input('Min width px', min_value=0, value=0, step=10, key='img_minw')
    with col4:
        cols_per_row = st.selectbox('Columns', [2, 3, 4, 5, 6], index=2, key='img_cols')

    # Apply filters
    filtered_imgs = images
    if img_search:
        filtered_imgs = [i for i in filtered_imgs if img_search.lower() in i.url.lower()]
    if mime_filter != 'All':
        filtered_imgs = [i for i in filtered_imgs if i.mime_type == mime_filter]
    if min_w > 0:
        filtered_imgs = [i for i in filtered_imgs if i.width >= min_w]

    st.caption(f'{len(filtered_imgs):,} images  ·  click any image to open its source URL')

    if not filtered_imgs:
        st.info('No cached images match the current filters.')
    else:
        # Render image grid
        for row_start in range(0, len(filtered_imgs), cols_per_row):
            row_imgs = filtered_imgs[row_start:row_start + cols_per_row]
            cols = st.columns(cols_per_row)
            for col, img in zip(cols, row_imgs):
                with col:
                    try:
                        st.image(img.data, use_container_width=True)
                    except Exception:
                        st.warning('Cannot render')
                    label = img.url.split('?')[0].split('/')[-1][:30] or img.filename
                    st.caption(
                        f'[{label}]({img.url})  \n'
                        f'{img.width}×{img.height} · {img.size_bytes:,}B · {img.mime_type.split("/")[-1]}'
                    )
