#!/usr/bin/env python3
"""
GoRails Video Downloader

A minimal tool to download videos from the GoRails video series.
"""

import os
import sys
import re
import json
import getpass
import click
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt, Confirm

console = Console()


def log_verbose(message, ctx=None):
    """Log message only in verbose mode."""
    if ctx and ctx.obj.get('verbose', False):
        console.print(f"[dim]{message}[/dim]")


class GoRailsAuth:
    def __init__(self):
        self.config_file = Path.home() / '.gorails.json'
        self.session_id = None

    def load_session(self):
        """Load session ID from config file."""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    self.session_id = config.get('session_id')
                    return self.session_id
        except Exception as e:
            console.print(f"[yellow]Warning: Could not load config: {e}[/yellow]")
        return None

    def save_session(self, session_id, ctx=None):
        """Save session ID to config file."""
        try:
            config = {'session_id': session_id}
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            self.session_id = session_id
            log_verbose("Session saved successfully!", ctx)
        except Exception as e:
            console.print(f"[red]Error saving session: {e}[/red]")

    def get_credentials(self, session, ctx=None):
        """Get credentials from user input."""
        auth_text = Text("GoRails Authentication", style="bold blue")
        auth_text.append("\n\nYou need to authenticate to download videos.\n", style="dim")
        auth_text.append("Choose an option:", style="bold")

        console.print(Panel(
            auth_text,
            title="[bold green]Authentication Required[/bold green]"
        ))

        # Show authentication options clearly
        console.print("\n[bold]Authentication Options:[/bold]")
        console.print("1. Email and Password - Login with your GoRails account")
        console.print("2. Session Cookie - Provide _gorails_session cookie value")
        console.print("3. Use Saved Session - Load previously saved session")

        session_option = Prompt.ask(
            "\nChoose authentication method",
            choices=["1", "2", "3"],
            default="1"
        )

        if session_option == "1":
            console.print("\nOption 1: Email and Password")
            email = Prompt.ask("Email")
            password = getpass.getpass("Password")
            return self._login_with_credentials(email, password, session, ctx)

        elif session_option == "2":
            console.print("\nOption 2: Session ID")
            session_id = Prompt.ask("_gorails_session cookie value")
            self.save_session(session_id, ctx)
            return session_id

        elif session_option == "3":
            console.print("\nOption 3: Use saved session")
            saved_session = self.load_session()
            if saved_session:
                console.print("[green]Using saved session[/green]")
                return saved_session
            else:
                console.print("[red]No saved session found[/red]")
                return self.get_credentials(session, ctx)

    def _login_with_credentials(self, email, password, session, ctx=None):
        """Perform actual login to GoRails."""
        try:
            log_verbose(f"Attempting to login with email: {email}", ctx)
            
            # First, get the login page to extract CSRF token
            login_url = "https://gorails.com/users/sign_in"
            response = session.get(login_url)
            response.raise_for_status()
            
            # Parse the login page to get CSRF token
            soup = BeautifulSoup(response.content, 'html.parser')
            csrf_token = soup.find('meta', attrs={'name': 'csrf-token'})
            
            if not csrf_token:
                console.print("[red]Could not find CSRF token on login page[/red]")
                return None
            
            csrf_value = csrf_token.get('content')
            
            # Prepare login data
            login_data = {
                'authenticity_token': csrf_value,
                'user[email]': email,
                'user[password]': password,
                'user[remember_me]': '1',  # Remember me for session persistence
                'commit': 'Log in'
            }
            
            # Perform login request
            log_verbose("Submitting login request...", ctx)
            login_response = session.post(login_url, data=login_data)
            
            # Check if login was successful
            if login_response.status_code in [200, 302]:
                # Check if we have a session cookie
                session_cookie = session.cookies.get('_gorails_session')
                if session_cookie:
                    log_verbose("Login successful!", ctx)
                    self.save_session(session_cookie, ctx)
                    return session_cookie
                else:
                    console.print("[red]Login failed: No session cookie received[/red]")
                    return None
            else:
                console.print(f"[red]Login failed: HTTP {login_response.status_code}[/red]")
                # Try to extract error message from response
                try:
                    error_soup = BeautifulSoup(login_response.content, 'html.parser')
                    error_msg = error_soup.find('div', class_='alert') or error_soup.find('div', class_='error')
                    if error_msg:
                        console.print(f"[red]Error: {error_msg.get_text(strip=True)}[/red]")
                except:
                    pass
                return None
                
        except Exception as e:
            console.print(f"[red]Login error: {e}[/red]")
            return None


class GoRailsDownloader:
    def __init__(self, output_dir="downloads"):
        self.output_dir = output_dir
        self.auth = GoRailsAuth()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

    def authenticate(self, ctx=None):
        """Authenticate with GoRails."""
        # Try to load saved session first
        session_id = self.auth.load_session()
        
        if not session_id:
            session_id = self.auth.get_credentials(self.session, ctx)
        
        if session_id:
            # Set the session cookie
            self.session.cookies.set('_gorails_session', session_id, domain='gorails.com')
            log_verbose("Authentication successful!", ctx)
            return True
        else:
            console.print("[red]Authentication failed![/red]")
            return False

    def get_video_info(self, url):
        """Extract video information from GoRails page."""
        try:
            console.print(f"Fetching video page: {url}")
            response = self.session.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Extract title
            title_elem = soup.find('h1')
            title = title_elem.get_text(strip=True) if title_elem else "Unknown Title"

            # Find download link
            download_link = soup.find('a', href=re.compile(r'/download'))
            if not download_link:
                console.print("[red]No download link found on the page[/red]")
                return None

            download_url = urljoin(url, download_link['href'])

            return {
                'title': title,
                'download_url': download_url,
                'page_url': url
            }

        except Exception as e:
            console.print(f"[red]Error extracting video info: {e}[/red]")
            return None

    def get_direct_video_url(self, download_url):
        """Follow redirect to get direct video URL."""
        try:
            console.print(f"Following download redirect from {download_url}", end="")
            response = self.session.get(download_url, allow_redirects=True)
            response.raise_for_status()

            # The final URL should be the direct video URL
            console.print(f" → {response.url}")
            return response.url

        except Exception as e:
            console.print(f"[red]Error getting direct video URL: {e}[/red]")
            return None

    def download_video(self, url, position=None, force=False):
        """Download a single video from the given URL."""
        try:
            # Get video info
            video_info = self.get_video_info(url)
            if not video_info:
                return None

            # Get direct video URL
            direct_url = self.get_direct_video_url(video_info['download_url'])
            if not direct_url:
                return None

            # Download the video
            return self._download_file(direct_url, video_info['title'], position, force)

        except Exception as e:
            console.print(f"[red]Error downloading video: {e}[/red]")
            return None

    def _download_file(self, url, title, position=None, force=False):
        """Download a file with progress tracking."""
        try:
            # Clean filename
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
            
            # Include position in filename if provided
            if position is not None:
                filename = f"{position:02d}_{safe_title}.mp4"
            else:
                filename = f"{safe_title}.mp4"
                
            filepath = os.path.join(self.output_dir, filename)

            # Check if file already exists
            if os.path.exists(filepath) and not force:
                console.print(f"[yellow]File already exists, skipping: {filename}[/yellow]")
                return {
                    'title': title,
                    'filename': filename,
                    'filepath': filepath,
                    'size': os.path.getsize(filepath),
                    'skipped': True
                }

            console.print(f"[green]Downloading: {title}[/green] from [red]{url}[/red]")

            # Start the download with streaming
            response = self.session.get(url, stream=True)
            response.raise_for_status()

            # Get file size
            total_size = int(response.headers.get('content-length', 0))

            with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    TimeRemainingColumn(),
                    console=console
            ) as progress:

                task = progress.add_task(f"Downloading {filename}", total=total_size)

                with open(filepath, 'wb') as f:
                    downloaded = 0
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            progress.update(task, completed=downloaded)

                progress.update(task, description=f"Downloaded {filename}")

            console.print(f"[green]Successfully downloaded: {filename}[/green]")
            return {
                'title': title,
                'filename': filename,
                'filepath': filepath,
                'size': total_size
            }

        except Exception as e:
            console.print(f"[red]Error downloading file: {e}[/red]")
            return None

    def download_playlist(self, playlist_url, force=False):
        """Download all videos from a playlist."""
        try:
            console.print(f"Fetching playlist page: {playlist_url}")
            response = self.session.get(playlist_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find all episode links in the playlist
            episode_links = []

            # Look for episode links in the sidebar or main content
            episode_elements = soup.find_all('a', href=re.compile(r'/episodes/'))

            for elem in episode_elements:
                href = elem.get('href')
                if href and '/episodes/' in href:
                    full_url = urljoin(playlist_url, href)
                    if full_url not in episode_links:
                        episode_links.append(full_url)

            if not episode_links:
                console.print("[red]No episode links found in playlist[/red]")
                return None

            console.print(f"Found {len(episode_links)} episodes")

            downloaded_videos = []
            skipped_count = 0
            for i, episode_url in enumerate(episode_links, 1):
                console.print(f"\nDownloading episode {i}/{len(episode_links)}")
                result = self.download_video(episode_url, position=i, force=force)
                if result:
                    if result.get('skipped', False):
                        skipped_count += 1
                    downloaded_videos.append(result)

            return {
                'playlist_url': playlist_url,
                'total_episodes': len(episode_links),
                'downloaded': len(downloaded_videos) - skipped_count,
                'skipped': skipped_count,
                'videos': downloaded_videos
            }

        except Exception as e:
            console.print(f"[red]Error downloading playlist: {e}[/red]")
            return None

    def get_series_list(self):
        """Fetch and parse all series from the GoRails series page."""
        try:
            console.print("Fetching series list from https://gorails.com/series")
            response = self.session.get("https://gorails.com/series")
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find all series articles
            series_articles = soup.find_all('article', class_=lambda x: x and 'p-6' in x)
            
            series_list = []
            for article in series_articles:
                # Find the series link
                series_link = article.find('a', href=re.compile(r'/series/'))
                if series_link:
                    href = series_link.get('href')
                    title = series_link.get_text(strip=True)
                    
                    if href and title:
                        full_url = urljoin("https://gorails.com", href)
                        series_list.append({
                            'title': title,
                            'url': full_url,
                            'slug': href.split('/')[-1] if href else title.lower().replace(' ', '-')
                        })

            console.print(f"Found {len(series_list)} series")
            return series_list

        except Exception as e:
            console.print(f"[red]Error fetching series list: {e}[/red]")
            return []

    def download_all_series(self, force=False):
        """Download all series from GoRails, each in its own directory."""
        try:
            # Get the list of all series
            series_list = self.get_series_list()
            
            if not series_list:
                console.print("[red]No series found[/red]")
                return None

            console.print(f"\n[bold blue]Starting download of {len(series_list)} series[/bold blue]")
            
            downloaded_series = []
            for i, series in enumerate(series_list, 1):
                console.print(f"\n[bold]Series {i}/{len(series_list)}: {series['title']}[/bold]")
                
                # Create a subdirectory for this series
                series_dir = os.path.join(self.output_dir, series['slug'])
                os.makedirs(series_dir, exist_ok=True)
                
                # Create a temporary downloader for this series
                series_downloader = GoRailsDownloader(series_dir)
                series_downloader.session = self.session  # Reuse the authenticated session
                
                # Download the series
                result = series_downloader.download_playlist(series['url'], force=force)
                if result:
                    downloaded_series.append({
                        'title': series['title'],
                        'url': series['url'],
                        'slug': series['slug'],
                        'total_episodes': result.get('total_episodes', 0),
                        'downloaded': result.get('downloaded', 0),
                        'skipped': result.get('skipped', 0),
                        'videos': result.get('videos', [])
                    })
                    console.print(f"[green]✓ Downloaded series: {series['title']} ({result.get('downloaded', 0)}/{result.get('total_episodes', 0)} episodes, {result.get('skipped', 0)} skipped)[/green]")
                else:
                    console.print(f"[red]✗ Failed to download series: {series['title']}[/red]")

            return {
                'total_series': len(series_list),
                'downloaded_series': len(downloaded_series),
                'series': downloaded_series
            }

        except Exception as e:
            console.print(f"[red]Error downloading all series: {e}[/red]")
            return None


@click.group()
@click.option('--output-dir', '-o', default='downloads', 
              help='Output directory for downloaded videos')
@click.option('--verbose', '-v', is_flag=True, default=False,
              help='Enable verbose logging')
@click.option('--force', '-f', is_flag=True, default=False,
              help='Force download and overwrite existing files')
@click.pass_context
def cli(ctx, output_dir, verbose, force):
    """GoRails Video Downloader - Download videos from GoRails series."""
    ctx.ensure_object(dict)
    ctx.obj['downloader'] = GoRailsDownloader(output_dir)
    ctx.obj['verbose'] = verbose
    ctx.obj['force'] = force


@cli.command()
@click.argument('url')
@click.pass_context
def video(ctx, url):
    """Download a single video."""
    downloader = ctx.obj['downloader']
    force = ctx.obj.get('force', False)

    console.print(Panel(
        Text("GoRails Video Downloader", style="bold blue"),
        title="Downloading single video..."
    ))

    # Authenticate first
    if not downloader.authenticate(ctx):
        console.print("[red]Authentication required to download videos[/red]")
        sys.exit(1)

    info = downloader.download_video(url, force=force)
    if info:
        if info.get('skipped', False):
            console.print(f"[yellow]File already exists, skipped: {info.get('title', 'Unknown')}[/yellow]")
        else:
            console.print(f"[green]Successfully downloaded: {info.get('title', 'Unknown')}[/green]")
    else:
        console.print("[red]Failed to download video[/red]")
        sys.exit(1)


@cli.command()
@click.argument('playlist_url')
@click.pass_context
def playlist(ctx, playlist_url):
    """Download all videos from a playlist."""
    downloader = ctx.obj['downloader']
    force = ctx.obj.get('force', False)

    if not downloader.authenticate(ctx):
        console.print("[red]Authentication required to download videos[/red]")
        sys.exit(1)

    info = downloader.download_playlist(playlist_url, force=force)
    if info:
        console.print(f"[green]Successfully downloaded playlist: {info.get('downloaded', 0)}/{info.get('total_episodes', 0)} episodes, {info.get('skipped', 0)} skipped[/green]")
    else:
        console.print("[red]Failed to download playlist[/red]")
        sys.exit(1)


@cli.command()
@click.pass_context
def all_series(ctx):
    """Download all series from GoRails, each in its own directory."""
    downloader = ctx.obj['downloader']
    force = ctx.obj.get('force', False)

    console.print(Panel(
        Text("GoRails Video Downloader", style="bold blue"),
        title="Downloading all series..."
    ))

    if not downloader.authenticate(ctx):
        console.print("[red]Authentication required to download videos[/red]")
        sys.exit(1)

    info = downloader.download_all_series(force=force)
    if info:
        console.print(f"[green]Successfully downloaded {info.get('downloaded_series', 0)}/{info.get('total_series', 0)} series[/green]")
    else:
        console.print("[red]Failed to download series[/red]")
        sys.exit(1)


@cli.command()
@click.pass_context
def auth(ctx):
    """Manage authentication for GoRails."""
    downloader = GoRailsDownloader()
    
    console.print(Panel(
        Text("GoRails Authentication Manager", style="bold blue"),
        title="Manage your authentication settings"
    ))
    
    if downloader.authenticate(ctx):
        console.print("[green]Authentication successful![/green]")
    else:
        console.print("[red]Authentication failed![/red]")
        sys.exit(1)


@cli.command()
def info():
    """Show information about the GoRails downloader."""
    info_text = Text("GoRails Video Downloader", style="bold blue")
    info_text.append("\n\nA minimal tool to download videos from the GoRails video series.\n", style="dim")
    info_text.append("Usage:", style="bold")
    info_text.append("\n  python gorails_downloader.py video <URL>     - Download single video")
    info_text.append("\n  python gorails_downloader.py playlist <URL>  - Download playlist")
    info_text.append("\n  python gorails_downloader.py all-series      - Download all series")
    info_text.append("\n  python gorails_downloader.py auth            - Manage authentication")
    info_text.append("\n  python gorails_downloader.py info            - Show this info")
    info_text.append("\n\nOptions:", style="bold")
    info_text.append("\n  --force, -f              - Force download and overwrite existing files")
    info_text.append("\n  --output-dir, -o <dir>   - Output directory for downloads")
    info_text.append("\n  --verbose, -v            - Enable verbose logging")

    console.print(Panel(
        info_text,
        title="[bold green]Info[/bold green]"
    ))


if __name__ == '__main__':
    cli()
