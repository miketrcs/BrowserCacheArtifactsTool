"""
Data classes for Chrome browser artifacts.
"""
import datetime
from typing import Optional


class URLItem:
    def __init__(self, url, title, visit_time, last_visit_time, visit_count,
                 typed_count, transition=None, visit_duration=None,
                 transition_friendly=None, visit_source=None):
        self.row_type = 'url'
        self.url = url
        self.title = title
        self.timestamp = visit_time
        self.visit_time = visit_time
        self.last_visit_time = last_visit_time
        self.visit_count = visit_count
        self.typed_count = typed_count
        self.transition = transition
        self.visit_duration = visit_duration
        self.transition_friendly = transition_friendly
        self.visit_source = visit_source

    def __repr__(self):
        return f'<URLItem {self.url!r} @ {self.visit_time}>'


class DownloadItem:
    def __init__(self, url, target_path, start_time, end_time,
                 received_bytes, total_bytes, state,
                 state_friendly=None, danger_type=None,
                 interrupt_reason=None, opened=None):
        self.row_type = 'download'
        self.url = url
        self.timestamp = start_time
        self.target_path = target_path
        self.start_time = start_time
        self.end_time = end_time
        self.received_bytes = received_bytes
        self.total_bytes = total_bytes
        self.state = state
        self.state_friendly = state_friendly
        self.danger_type = danger_type
        self.interrupt_reason = interrupt_reason
        self.opened = opened

    def __repr__(self):
        return f'<DownloadItem {self.url!r} @ {self.start_time}>'


class CookieItem:
    def __init__(self, host_key, path, name, value, creation_utc,
                 last_access_utc, expires_utc, last_update_utc,
                 secure, httponly, persistent=None, has_expires=None,
                 top_frame_site_key=None):
        self.row_type = 'cookie'
        self.timestamp = creation_utc
        self.host_key = host_key
        self.path = path
        self.name = name
        self.value = value
        self.creation_utc = creation_utc
        self.last_access_utc = last_access_utc
        self.expires_utc = expires_utc
        self.last_update_utc = last_update_utc
        self.secure = secure
        self.httponly = httponly
        self.persistent = persistent
        self.has_expires = has_expires
        self.top_frame_site_key = top_frame_site_key

    def __repr__(self):
        return f'<CookieItem {self.host_key!r} {self.name!r}>'


class BookmarkItem:
    def __init__(self, name, url, date_added, parent_folder):
        self.row_type = 'bookmark'
        self.timestamp = date_added
        self.name = name
        self.url = url
        self.date_added = date_added
        self.parent_folder = parent_folder

    def __repr__(self):
        return f'<BookmarkItem {self.name!r} {self.url!r}>'


class BookmarkFolderItem:
    def __init__(self, name, date_added, date_modified, parent_folder):
        self.row_type = 'bookmark folder'
        self.timestamp = date_added
        self.name = name
        self.date_added = date_added
        self.date_modified = date_modified
        self.parent_folder = parent_folder

    def __repr__(self):
        return f'<BookmarkFolderItem {self.name!r}>'
