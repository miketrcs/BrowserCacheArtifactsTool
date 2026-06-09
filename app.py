"""
BrowserCacheArtifactsTool — Streamlit GUI
Supports Chrome, Edge, Firefox, and Safari on macOS.
"""
import datetime
import os
import subprocess
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
from chrome_artifacts.safari_parsers import (
    parse_safari_history, parse_safari_downloads,
    parse_safari_cookies, parse_safari_bookmarks,
    default_paths as safari_default_paths,
)
from chrome_artifacts.safari_cache import scan_safari_cache
from chrome_artifacts.firefox_parsers import (
    parse_firefox_history, parse_firefox_downloads,
    parse_firefox_cookies, parse_firefox_bookmarks,
    default_profile_path as _firefox_default_profile,
)
from chrome_artifacts.firefox_cache import scan_firefox_cache

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title='Browser Artifacts',
    page_icon='🔍',
    layout='wide',
    initial_sidebar_state='expanded',
)

_home = Path(os.environ.get('REAL_HOME', str(Path.home())))
DEFAULT_CHROME_PROFILE   = str(_home / 'Library/Application Support/Google/Chrome/Default')
DEFAULT_EDGE_PROFILE     = str(_home / 'Library/Application Support/Microsoft Edge/Default')
DEFAULT_FIREFOX_PROFILE  = _firefox_default_profile()
DEFAULT_SAFARI_ROOT      = str(_home / 'Library/Safari')


def _probe_permissions(test_path: Path) -> bool:
    """
    Try to read one byte from test_path.
    Returns True if accessible (or file simply doesn't exist yet).
    Returns False only on PermissionError.
    The actual read attempt is what triggers the macOS TCC consent dialog.
    """
    if not test_path.exists():
        return True  # Missing file is not a permissions problem
    try:
        with open(test_path, 'rb') as f:
            f.read(1)
        return True
    except PermissionError:
        return False
    except OSError:
        return True  # Other OS errors are not permission-related


def _open_full_disk_access_settings():
    """Open System Settings directly to the Full Disk Access page."""
    subprocess.run([
        'open',
        'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'
    ], check=False)

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
    return pd.DataFrame([{
        'Added':   fmt_dt(i.date_added),
        'Name':    i.name or '',
        'URL':     getattr(i, 'url', ''),
        'Folder':  i.parent_folder or '',
        'Type':    i.row_type,
    } for i in items])


# ---------------------------------------------------------------------------
# Sidebar — browser + profile config
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title('🔍 Browser Artifacts')
    st.caption('macOS browser artifact browser')
    st.divider()

    browser = st.radio(
        'Browser',
        ['Chrome', 'Edge', 'Firefox', 'Safari'],
        horizontal=True,
    )

    if browser in ('Chrome', 'Edge'):
        default_profile = DEFAULT_CHROME_PROFILE if browser == 'Chrome' else DEFAULT_EDGE_PROFILE
        profile_path = st.text_input(
            'Profile directory',
            value=default_profile,
            help='Path to the browser profile folder (usually "Default")',
        )
        decrypt = st.toggle(
            'Decrypt cookies',
            value=False,
            help='Retrieve encryption key from macOS Keychain to decrypt cookie values',
        )
        no_copy = st.toggle(
            'No file copy',
            value=False,
            help='Read DB files directly — faster, but may fail if the browser is open',
        )
    elif browser == 'Firefox':
        profile_path = st.text_input(
            'Profile directory',
            value=DEFAULT_FIREFOX_PROFILE,
            help='Path to Firefox profile folder (auto-detected from profiles.ini)',
        )
        decrypt = False
        no_copy = False
        st.caption('Firefox cookies are stored as plaintext — no decryption needed.')
    else:
        profile_path = st.text_input(
            'Safari data directory',
            value=DEFAULT_SAFARI_ROOT,
            help='Path to Safari data folder (usually ~/Library/Safari)',
        )
        decrypt = False
        no_copy = False

    st.divider()
    load_btn = st.button('Load Profile', type='primary', width="stretch")

    st.divider()
    st.caption('Export')
    export_name = st.text_input('Filename', value='browser_artifacts.db')
    export_btn = st.button('Export to SQLite', width="stretch")


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for key in ('history', 'downloads', 'cookies', 'bookmarks', 'images', 'version', 'loaded', 'browser', 'perm_error', 'profile'):
    if key not in st.session_state:
        st.session_state[key] = None

st.session_state.setdefault('loaded', False)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
if load_btn:
    profile = str(Path(profile_path).expanduser().resolve())

    if not Path(profile).is_dir():
        st.error(f'Directory not found: `{profile}`')
    else:
        st.session_state.browser = browser
        st.session_state.profile = profile

        if browser in ('Chrome', 'Edge'):
            # Probe permissions — the read attempt triggers the macOS dialog if needed
            if not _probe_permissions(Path(profile) / 'History'):
                st.session_state.perm_error = browser
                st.session_state.loaded = True
                st.rerun()

            st.session_state.perm_error = False
            with st.spinner(f'Detecting {browser} version…'):
                version = detect_version(profile, no_copy=no_copy)
            st.session_state.version = version

            decryptor = None
            if decrypt:
                try:
                    from chrome_artifacts.decrypt import MacDecryptor
                    decryptor = MacDecryptor(browser=browser)
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
                st.session_state.images = scan_cache(cache_dir, max_results=0, scan_limit=30000)

        elif browser == 'Firefox':
            # Probe permissions — the read attempt triggers the macOS dialog if needed
            if not _probe_permissions(Path(profile) / 'places.sqlite'):
                st.session_state.perm_error = 'Firefox'
                st.session_state.loaded = True
                st.rerun()

            st.session_state.perm_error = False
            st.session_state.version = ['Firefox']

            with st.spinner('Parsing Firefox history…'):
                st.session_state.history = parse_firefox_history(profile)
            with st.spinner('Parsing Firefox downloads…'):
                st.session_state.downloads = parse_firefox_downloads(profile)
            with st.spinner('Parsing Firefox cookies…'):
                st.session_state.cookies = parse_firefox_cookies(profile)
            with st.spinner('Parsing Firefox bookmarks…'):
                st.session_state.bookmarks = parse_firefox_bookmarks(profile)
            with st.spinner('Scanning Firefox cache for images…'):
                st.session_state.images = scan_firefox_cache(profile, max_results=0, scan_limit=30000)

        else:  # Safari
            # Probe both the Safari root and the sandboxed container (WebKitCache / cookies).
            # Each read attempt triggers the macOS TCC dialog for its respective location.
            _safari_container = (
                _home / 'Library/Containers/com.apple.Safari/Data/Library/Caches/com.apple.Safari'
            )
            _safari_cookie_path = (
                _home / 'Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies'
            )
            if (not _probe_permissions(Path(profile) / 'History.db') or
                    not _probe_permissions(_safari_cookie_path) or
                    not _probe_permissions(_safari_container / 'Cache.db')):
                st.session_state.perm_error = 'Safari'
                st.session_state.loaded = True
                st.rerun()

            st.session_state.perm_error = False
            st.session_state.version = ['Safari']

            with st.spinner('Parsing Safari history…'):
                st.session_state.history = parse_safari_history(profile)
            with st.spinner('Parsing Safari downloads…'):
                st.session_state.downloads = parse_safari_downloads(profile)
            with st.spinner('Parsing Safari cookies…'):
                st.session_state.cookies = parse_safari_cookies(profile)
            with st.spinner('Parsing Safari bookmarks…'):
                st.session_state.bookmarks = parse_safari_bookmarks(profile)
            with st.spinner('Scanning Safari cache for images…'):
                st.session_state.images = scan_safari_cache(profile, max_results=0)

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
    st.markdown('## Browser Artifact Browser')
    st.markdown(
        'Select a browser in the sidebar, enter the profile path, and click **Load Profile**.\n\n'
        '**Default locations on macOS:**\n'
        '```\n'
        'Chrome:  ~/Library/Application Support/Google/Chrome/Default\n'
        'Edge:    ~/Library/Application Support/Microsoft Edge/Default\n'
        'Firefox: ~/Library/Application Support/Firefox/Profiles/<id>.default-release\n'
        'Safari:  ~/Library/Safari\n'
        '```'
    )
    st.info('Tip: close the browser before loading to ensure all databases are accessible.')
    st.stop()

# Permission error banner — shown for any browser that was denied
if st.session_state.perm_error:
    denied_browser = st.session_state.perm_error  # browser name string

    if denied_browser == 'Safari':
        st.error(
            f'**macOS Full Disk Access required for {denied_browser} data.**\n\n'
            'Safari\'s files are protected by macOS privacy controls. '
            'Grant **Full Disk Access** to Terminal (or your shell), then restart the app.'
        )
        if st.button('Open Privacy & Security Settings →'):
            _open_full_disk_access_settings()
        st.markdown(
            '**Steps:**\n'
            '1. Click the button above (or go to **System Settings → Privacy & Security → Full Disk Access**)\n'
            '2. Click **+** and add **Terminal** (or iTerm2 / whichever app you launched this from)\n'
            '3. Quit Terminal completely and relaunch\n'
            '4. Run `~/BrowserCacheArtifacts/run.sh` again'
        )
    else:
        st.error(
            f'**macOS privacy access required for {denied_browser} data.**\n\n'
            'A macOS permission dialog may have appeared — if so, click **Allow** and then '
            'click **Load Profile** again.\n\n'
            'If no dialog appeared, grant access manually:'
        )
        if st.button('Open Privacy & Security Settings →'):
            _open_full_disk_access_settings()
        st.markdown(
            '**Steps:**\n'
            '1. Go to **System Settings → Privacy & Security → Full Disk Access** '
            '(or **Files and Folders**)\n'
            '2. Click **+** and add **Terminal** (or whichever app you launched this from)\n'
            '3. Click **Load Profile** again — no restart needed'
        )
    st.divider()

# Summary metrics
active_browser = st.session_state.browser or 'Browser'
v = st.session_state.version
if isinstance(v, list) and len(v) > 1 and active_browser in ('Chrome', 'Edge'):
    version_str = f'{active_browser} version range: {v[0]}–{v[-1]}'
elif active_browser == 'Firefox':
    version_str = 'Firefox'
else:
    version_str = active_browser

st.caption(f'{version_str}  ·  Profile: `{st.session_state.profile or profile_path}`')

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('History',       f'{len(st.session_state.history or []):,}')
c2.metric('Downloads',     f'{len(st.session_state.downloads or []):,}')
c3.metric('Cookies',       f'{len(st.session_state.cookies or []):,}')
c4.metric('Bookmarks',     f'{len(st.session_state.bookmarks or []):,}')
c5.metric('Cached Images', f'{len(st.session_state.images or []):,}')

st.divider()

tab_hist, tab_dl, tab_cook, tab_bm, tab_img = st.tabs(
    ['📄 History', '⬇️ Downloads', '🍪 Cookies', '🔖 Bookmarks', '🖼️ Cached Images']
)

# ---- History ---------------------------------------------------------------
with tab_hist:
    items = st.session_state.history
    df = history_to_df(items)

    if df.empty:
        st.info('No history found.')
    else:
        row1 = st.columns([3, 1])
        row2 = st.columns([1, 1, 1])

        with row1[0]:
            search = st.text_input('Search URL or title', key='hist_search', placeholder='Filter…')
        with row1[1]:
            transitions = ['All'] + sorted(df['Transition'].unique().tolist())
            trans_filter = st.selectbox('Transition', transitions, key='hist_trans')

        with row2[0]:
            date_from = st.date_input('From date', value=None, key='hist_date_from')
        with row2[1]:
            date_to = st.date_input('To date', value=None, key='hist_date_to')
        with row2[2]:
            typed_only = st.checkbox('Typed URLs only', key='hist_typed')

        if search:
            mask = (df['URL'].str.contains(search, case=False, na=False) |
                    df['Title'].str.contains(search, case=False, na=False))
            df = df[mask]
        if trans_filter != 'All':
            df = df[df['Transition'] == trans_filter]
        if date_from:
            df = df[df['Visited'] >= str(date_from)]
        if date_to:
            df = df[df['Visited'] <= str(date_to) + ' 23:59:59']
        if typed_only:
            df = df[df['Typed'] > 0]

        st.caption(f'{len(df):,} records')
        st.dataframe(df, width="stretch", height=500,
                     column_config={
                         'URL': st.column_config.LinkColumn('URL'),
                         'Visits': st.column_config.NumberColumn('Visits', format='%d'),
                     })

# ---- Downloads -------------------------------------------------------------
with tab_dl:
    items = st.session_state.downloads
    df = downloads_to_df(items)

    if df.empty:
        st.info('No downloads found.')
    else:
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            search = st.text_input('Search URL or path', key='dl_search', placeholder='Filter…')
        with col2:
            states = ['All'] + sorted(df['State'].unique().tolist())
            state_filter = st.selectbox('State', states, key='dl_state')
        with col3:
            dangerous_only = st.checkbox('Dangerous only', key='dl_danger',
                                         help='Files flagged as dangerous (danger_type > 0)')

        if search:
            mask = (df['URL'].str.contains(search, case=False, na=False) |
                    df['Saved To'].str.contains(search, case=False, na=False))
            df = df[mask]
        if state_filter != 'All':
            df = df[df['State'] == state_filter]
        if dangerous_only:
            df = df[df['Danger'].apply(lambda v: v not in ('', '0', 'safe', 'None'))]

        st.caption(f'{len(df):,} records')
        st.dataframe(df, width="stretch", height=500,
                     column_config={
                         'URL': st.column_config.LinkColumn('URL'),
                         'Received': st.column_config.NumberColumn('Received', format='%d bytes'),
                         'Total':    st.column_config.NumberColumn('Total',    format='%d bytes'),
                     })

# ---- Cookies ---------------------------------------------------------------
with tab_cook:
    items = st.session_state.cookies
    df = cookies_to_df(items)

    if df.empty:
        st.info('No cookies found.')
    else:
        col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
        with col1:
            search = st.text_input('Search host or name', key='cook_search', placeholder='Filter…')
        with col2:
            secure_only = st.checkbox('Secure only', key='cook_secure')
        with col3:
            persistent_only = st.checkbox('Persistent only', key='cook_persist')
        with col4:
            hide_expired = st.checkbox('Hide expired', key='cook_hide_expired')

        if search:
            mask = (df['Host'].str.contains(search, case=False, na=False) |
                    df['Name'].str.contains(search, case=False, na=False))
            df = df[mask]
        if secure_only:
            df = df[df['Secure']]
        if persistent_only:
            df = df[df['Persistent']]
        if hide_expired:
            now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            df = df[(df['Expires'] == '') | (df['Expires'] > now_str)]

        st.caption(f'{len(df):,} records')
        st.dataframe(df, width="stretch", height=500)

# ---- Bookmarks -------------------------------------------------------------
with tab_bm:
    items = st.session_state.bookmarks
    df = bookmarks_to_df(items)

    if df.empty or 'Type' not in df.columns:
        st.info('No bookmarks found.')
    else:
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
        st.dataframe(filtered.drop(columns=['Type']), width="stretch", height=500,
                     column_config={
                         'URL': st.column_config.LinkColumn('URL'),
                     })

# ---- Cached Images ---------------------------------------------------------
with tab_img:
    images = st.session_state.images or []

    row1 = st.columns([3, 1, 1, 1, 1, 1, 1])
    row2 = st.columns([1, 1, 1, 1, 5])

    with row1[0]:
        img_search = st.text_input('Filter by URL', key='img_search', placeholder='e.g. amazon, youtube…')
    with row1[1]:
        mime_types = ['All'] + sorted({i.mime_type for i in images})
        mime_filter = st.selectbox('Type', mime_types, key='img_mime')
    with row1[2]:
        min_w = st.number_input('Min width px', min_value=0, value=0, step=10, key='img_minw')
    with row1[3]:
        min_h = st.number_input('Min height px', min_value=0, value=0, step=10, key='img_minh')
    with row1[4]:
        dates = sorted(
            {i.cached_at.strftime('%Y-%m-%d') for i in images if getattr(i, 'cached_at', None)},
            reverse=True,
        )
        date_filter = st.selectbox('Cached date', ['All'] + dates, key='img_date')
    with row1[5]:
        show_limit = st.selectbox('Show', [200, 500, 1000, 2000, 'All'], index=0, key='img_limit')
    with row1[6]:
        cols_per_row = st.selectbox('Columns', [2, 3, 4, 5, 6], index=2, key='img_cols')

    # Apply filters
    filtered_imgs = images
    if img_search:
        filtered_imgs = [i for i in filtered_imgs if img_search.lower() in i.url.lower()]
    if mime_filter != 'All':
        filtered_imgs = [i for i in filtered_imgs if i.mime_type == mime_filter]
    if min_w > 0:
        filtered_imgs = [i for i in filtered_imgs if i.width >= min_w]
    if min_h > 0:
        filtered_imgs = [i for i in filtered_imgs if i.height >= min_h]
    if date_filter != 'All':
        filtered_imgs = [i for i in filtered_imgs
                         if getattr(i, 'cached_at', None)
                         and i.cached_at.strftime('%Y-%m-%d') == date_filter]

    display_imgs = filtered_imgs if show_limit == 'All' else filtered_imgs[:int(show_limit)]
    total_str = f'{len(filtered_imgs):,}' + (f' (showing {len(display_imgs):,})' if len(display_imgs) < len(filtered_imgs) else '')
    st.caption(f'{total_str} images  ·  sorted newest first  ·  click any image to open its source URL')

    if not display_imgs:
        st.info('No cached images match the current filters.')
    else:
        for row_start in range(0, len(display_imgs), cols_per_row):
            row_imgs = display_imgs[row_start:row_start + cols_per_row]
            cols = st.columns(cols_per_row)
            for grid_idx, (col, img) in enumerate(zip(cols, row_imgs), start=row_start):
                with col:
                    try:
                        st.image(img.data, width='stretch')
                    except Exception:
                        st.warning('Cannot render')
                    label = img.url.split('?')[0].split('/')[-1][:30] or getattr(img, 'filename', img.url[:20])
                    date_str = img.cached_at.strftime('%Y-%m-%d %H:%M') if getattr(img, 'cached_at', None) else ''
                    st.caption(
                        f'[{label}]({img.url})  \n'
                        f'{img.width}×{img.height} · {img.size_bytes:,}B · {img.mime_type.split("/")[-1]}'
                        + (f'  \n{date_str}' if date_str else '')
                    )
                    st.download_button(
                        label='Save',
                        data=img.data,
                        file_name=label or 'image',
                        mime=img.mime_type,
                        key=f'dl_{grid_idx}_{getattr(img, "filename", "")}_{img.url[:40]}',
                        width='stretch',
                    )
