#!/usr/bin/env python3
"""
chrome-artifacts - macOS browser artifact browser
Parses: history, downloads, cookies, bookmarks

Usage examples:
  python main.py -i "~/Library/Application Support/Google/Chrome/Default/"
  python main.py -i "~/Library/Application Support/Google/Chrome/Default/" -o results.db
  python main.py -i "~/Library/Application Support/Google/Chrome/Default/" --type history --limit 100
  python main.py -i "~/Library/Application Support/Google/Chrome/Default/" --type cookies --decrypt
  python main.py --browser firefox -i "~/Library/Application Support/Firefox/Profiles/abc.default-release"
  python main.py --browser safari -i "~/Library/Safari"
"""
import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from chrome_artifacts import __version__
from chrome_artifacts.parsers import detect_version, parse_history, parse_downloads, parse_cookies, parse_bookmarks
from chrome_artifacts.output import (
    display_history, display_downloads, display_cookies, display_bookmarks,
    export_sqlite, console,
)

ALL_TYPES = ('history', 'downloads', 'cookies', 'bookmarks')


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='browser-artifacts',
        description='macOS browser artifact browser — Chrome, Edge, Firefox, Safari',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Default profile locations on macOS:
  Chrome:  ~/Library/Application Support/Google/Chrome/Default/
  Edge:    ~/Library/Application Support/Microsoft Edge/Default/
  Firefox: ~/Library/Application Support/Firefox/Profiles/<id>.default-release/
  Safari:  ~/Library/Safari/
        ''',
    )
    p.add_argument('-i', '--input', required=True,
                   help='Path to browser profile directory (or Safari root)')
    p.add_argument('--browser', choices=['chrome', 'edge', 'firefox', 'safari'],
                   default='chrome',
                   help='Browser to parse (default: chrome)')
    p.add_argument('-o', '--output',
                   help='Export to SQLite database (e.g. results.db)')
    p.add_argument('--type', dest='types', nargs='+',
                   choices=list(ALL_TYPES) + ['all'], default=['all'],
                   help='Artifact type(s) to parse (default: all)')
    p.add_argument('--decrypt', action='store_true',
                   help='Decrypt cookie values using macOS Keychain (Chrome/Edge only)')
    p.add_argument('--limit', type=int, default=50,
                   help='Max rows to display per artifact type (default: 50)')
    p.add_argument('--no-copy', action='store_true',
                   help="Read DB files directly (faster, but may fail if browser is open; Chrome/Edge only)")
    p.add_argument('-v', '--verbose', action='store_true',
                   help='Enable debug logging')
    return p


def banner():
    txt = Text()
    txt.append('browser-artifacts', style='bold green')
    txt.append(f'  v{__version__}', style='dim')
    txt.append('\n  macOS browser artifact browser', style='white')
    console.print(Panel(txt, expand=False))


def main():
    args = build_parser().parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format='%(levelname)s: %(message)s',
    )

    banner()

    profile = str(Path(args.input).expanduser().resolve())
    if not Path(profile).is_dir():
        console.print(f'[red]Profile directory not found:[/red] {profile}')
        sys.exit(1)

    types = set(ALL_TYPES if 'all' in args.types else args.types)
    browser = args.browser.lower()
    no_copy = args.no_copy

    console.print(f'[dim]Browser:[/dim] {browser}')
    console.print(f'[dim]Profile:[/dim] {profile}')

    history = downloads = cookies = bookmarks = None

    if browser in ('chrome', 'edge'):
        version = detect_version(profile, no_copy=no_copy)
        console.print(f'[dim]Detected version range:[/dim] {version[0]}–{version[-1]}\n')

        decryptor = None
        if args.decrypt and 'cookies' in types:
            try:
                from chrome_artifacts.decrypt import MacDecryptor
                decryptor = MacDecryptor(browser=browser.capitalize())
                console.print('[green]Cookie decryption enabled[/green]\n')
            except Exception as e:
                console.print(f'[yellow]Warning: could not initialise decryptor: {e}[/yellow]\n')

        if 'history' in types:
            with console.status('Parsing history…'):
                history = parse_history(profile, version, no_copy=no_copy)
        if 'downloads' in types:
            with console.status('Parsing downloads…'):
                downloads = parse_downloads(profile, version, no_copy=no_copy)
        if 'cookies' in types:
            with console.status('Parsing cookies…'):
                cookies = parse_cookies(profile, version, decryptor=decryptor, no_copy=no_copy)
        if 'bookmarks' in types:
            with console.status('Parsing bookmarks…'):
                bookmarks = parse_bookmarks(profile, version)

    elif browser == 'firefox':
        from chrome_artifacts.firefox_parsers import (
            parse_firefox_history, parse_firefox_downloads,
            parse_firefox_cookies, parse_firefox_bookmarks,
        )
        if 'history' in types:
            with console.status('Parsing Firefox history…'):
                history = parse_firefox_history(profile)
        if 'downloads' in types:
            with console.status('Parsing Firefox downloads…'):
                downloads = parse_firefox_downloads(profile)
        if 'cookies' in types:
            with console.status('Parsing Firefox cookies…'):
                cookies = parse_firefox_cookies(profile)
        if 'bookmarks' in types:
            with console.status('Parsing Firefox bookmarks…'):
                bookmarks = parse_firefox_bookmarks(profile)

    elif browser == 'safari':
        from chrome_artifacts.safari_parsers import (
            parse_safari_history, parse_safari_downloads,
            parse_safari_cookies, parse_safari_bookmarks,
        )
        if 'history' in types:
            with console.status('Parsing Safari history…'):
                history = parse_safari_history(profile)
        if 'downloads' in types:
            with console.status('Parsing Safari downloads…'):
                downloads = parse_safari_downloads(profile)
        if 'cookies' in types:
            with console.status('Parsing Safari cookies…'):
                cookies = parse_safari_cookies(profile)
        if 'bookmarks' in types:
            with console.status('Parsing Safari bookmarks…'):
                bookmarks = parse_safari_bookmarks(profile)

    # --- Display ---
    if history:
        display_history(history, limit=args.limit)
    if downloads:
        display_downloads(downloads, limit=args.limit)
    if cookies:
        display_cookies(cookies, limit=args.limit)
    if bookmarks:
        display_bookmarks(bookmarks, limit=args.limit)

    # --- Summary ---
    lines = []
    if history:   lines.append(f'  history:   {len(history):>6,} records')
    if downloads: lines.append(f'  downloads: {len(downloads):>6,} records')
    if cookies:   lines.append(f'  cookies:   {len(cookies):>6,} records')
    if bookmarks: lines.append(f'  bookmarks: {len(bookmarks):>6,} records')
    if lines:
        console.print('[bold]Totals:[/bold]\n' + '\n'.join(lines))

    # --- Export ---
    if args.output:
        out = str(Path(args.output).expanduser().resolve())
        with console.status(f'Exporting to {out}…'):
            export_sqlite(out,
                          history=history,
                          downloads=downloads,
                          cookies=cookies,
                          bookmarks=bookmarks)
        console.print(f'\n[green]Saved:[/green] {out}')


if __name__ == '__main__':
    main()
