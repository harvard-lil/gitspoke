import os
import json
from pathlib import Path
import click
import re
import subprocess
import tempfile
import logging
import requests
from typing import Optional, Iterator, Any
from urllib.parse import urljoin
from ghapi.all import github_auth_device
import time

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".gitspoke" / "config.json"

endpoints = [
    ("issues.json", "issues?state=all"),
    ("issue_comments.json", "issues/comments"),
    ("labels.json", "labels"),
    ("milestones.json", "milestones?state=all"),
    ("pull_requests.json", "pulls?state=all"),
    ("pr_review_comments.json", "pulls/comments"),
    ("releases.json", "releases"),
    ("tags.json", "tags"),
    ("security_advisories.json", "security-advisories"),
    ("workflows.json", "actions/workflows", {"list_key": "workflows"}),
    ("stargazers.json", "stargazers"),
    ("watchers.json", "subscribers"),
    ("contributors.json", "contributors"),
    ("commit_comments.json", "comments"),
    ("forks.json", "forks"),
    ("branches.json", "branches"),
    ("pages.json", "pages", {"expect_404": True}),
    ("languages.json", "languages", {"paginate": False}),
]

valid_include_items = [
    "all", "repo_info", "bundle", "readme", "wiki"
] + [endpoint[0].split(".")[0] for endpoint in endpoints]

class GitHubAPI:
    BASE_URL = "https://api.github.com/"
    
    def __init__(
            self,
            token: Optional[str] = None,
            max_retries: int = 3,
            max_wait: int = 10
    ):
        self.max_retries = max_retries
        self.max_wait = max_wait
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHub-Downloader"
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def request(self, path: str, method: str = 'GET', **kwargs) -> Any:
        """Send a request to GitHub API."""
        url = urljoin(self.BASE_URL, path.lstrip("/"))

        for _ in range(self.max_retries):
            response = self.session.request(method, url, **kwargs)

            # handle rate limit
            if response.status_code in (403, 429) and response.headers.get('x-ratelimit-remaining') == '0':
                if retry_after := response.headers.get('retry-after'):
                    logger.debug(f"Secondary rate limit exceeded, using retry-after header. Waiting {retry_after} seconds...")
                    sleep_time = int(retry_after)
                else:
                    reset_time = response.headers['x-ratelimit-reset']
                    logger.debug(f"Primary rate limit exceeded, using x-ratelimit-reset header. Waiting {reset_time} - {time.time()} seconds...")
                    sleep_time = int(reset_time) - time.time()
                
                sleep_time = min(sleep_time+1, self.max_wait)
                logger.warning(f"Rate limit exceeded. Waiting {sleep_time:.1f} seconds...")
                time.sleep(sleep_time)
                continue
            
            break

        response.raise_for_status()
        return response

    def paginate(
            self, path: str,
            method: str = 'GET',
            list_key: str | None = None,
            **kwargs
    ) -> Iterator[Any]:
        """
        Paginate through all results of a GitHub API request.

        If list_key is provided, the items will be extracted from the response.
        For example, while many endpoints return a list of items, the workflows
        endpoint returns a dictionary with a 'workflows' key, so should use
        list_key='workflows'.
        """
        kwargs.setdefault('params', {})
        kwargs['params']['per_page'] = 100
        url = urljoin(self.BASE_URL, path.lstrip("/"))
        
        try:
            while True:
                response = self.request(url, method=method, **kwargs)
                items = response.json()
                if list_key:
                    items = items[list_key]
                yield from items
                
                if 'next' not in response.links:
                    break
                url = response.links['next']['url']
                
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 422 and "pagination is limited" in e.response.text.lower():
                logger.warning("Hit GitHub pagination limit. Some results may be incomplete.")
                return
            raise

class Downloader:
    def __init__(
            self,
            owner: str,
            repo_name: str,
            token: Optional[str] = None,
            max_retries: int = 3
    ):
        self.token = token
        self.owner = owner
        self.repo_name = repo_name
        self.api = GitHubAPI(token, max_retries)

    def write_api_response(
            self,
            path: Path,
            endpoint: str,
            paginate: bool = True,
            expect_404: bool = False,
            **kwargs
    ):
        """Write API response to a JSON file. By default paginates through all results."""
        if path.exists():
            logger.info(f"Skipping {path.name} - file already exists")
            return
            
        logger.info(f"Downloading {path.name}...")
        
        try:
            if paginate:
                results = list(self.api.paginate(endpoint, **kwargs))
            else:
                response = self.api.request(endpoint, **kwargs)
                results = response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404 and expect_404:
                results = [] if paginate else {}
            else:
                raise
        
        path.write_text(json.dumps(results, indent=2))
        
        if paginate:
            logger.debug(f"Wrote {len(results)} items to {path}")
        else:
            logger.debug(f"Wrote response to {path}")

    def download_git_repo(self, bundle_file: Path, extension: str = ".git"):
        """Download complete git repository using git protocol."""
        
        if bundle_file.exists():
            logger.info(f"{bundle_file.name} already exists, skipping...")
            return

        logger.info("Downloading complete git repository...")

        # Extract the HTTPS clone URL
        if self.token:
            clone_url = f"https://oauth2:{self.token}@github.com/{self.owner}/{self.repo_name}{extension}"
        else:
            clone_url = f"https://github.com/{self.owner}/{self.repo_name}{extension}"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                subprocess.run([
                    "git", "clone", "--mirror", clone_url, temp_dir
                ], check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                if "Repository not found" in e.stderr:
                    logger.warning(f"Repository not found: {self.owner}/{self.repo_name}{extension}")
                    return
                raise
            subprocess.run([
                "git", "bundle", "create", str(bundle_file.absolute()), "--all"
            ], cwd=temp_dir, check=True)

    def download_readme(self, output_dir: Path):
        """Download repository's preferred readme file in HTML format."""
        html_path = output_dir / "readme.html"
        if html_path.exists():
            logger.info("Readme already exists, skipping...")
            return
        
        logger.info("Downloading readme...")
        response = self.api.request(
            f'/repos/{self.owner}/{self.repo_name}/readme',
            headers={"Accept": "application/vnd.github.html+json"}
        )
        html_path.write_text(response.text)

    def download_repo(self, output_dir: Optional[Path] = None, include: Optional[list[str]] = None):
        """Download repository metadata and git content.
        
        Args:
            output_dir: Optional directory to store downloads. Defaults to current dir.
            include: List of elements to include.
        """
        # Parse include options
        if include is None:
            include = []
        include_set = set(include)
        
        # Check if repo exists
        repo_url = f'/repos/{self.owner}/{self.repo_name}'
        repo_info_path = output_dir / "repo_info.json"
        if repo_info_path.exists():
            # use cached repo info
            logger.debug(f"Using cached repo info for {self.owner}/{self.repo_name}")
            repo_info = json.loads(repo_info_path.read_text())
        else:
            # fetch repo info
            logger.debug(f"Fetching repo info for {self.owner}/{self.repo_name}")
            try:
                repo_info = self.api.request(repo_url).json()
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    logger.error(f"Repository {self.owner}/{self.repo_name} not found")
                    return
                else:
                    logger.error(f"Failed to load repository {self.owner}/{self.repo_name}: {e}")
                    return

            # Make output dir
            if output_dir is None:
                output_dir = Path.cwd() / self.owner / self.repo_name
            output_dir.mkdir(parents=True, exist_ok=True)

            # Save repository info
            repo_info_path.write_text(json.dumps(repo_info, indent=2))

        # Download the complete git repository if requested
        if "all" in include_set or "bundle" in include_set:
            self.download_git_repo(output_dir / f"git.bundle")

        # Download wiki if requested
        if "all" in include_set or "wiki" in include_set and repo_info.get('has_wiki'):
            self.download_git_repo(output_dir / f"wiki.bundle", ".wiki.git")

        # Download readme if requested
        if "all" in include_set or "readme" in include_set:
            self.download_readme(output_dir)

        # Download requested endpoints
        for endpoint in endpoints:
            filename = endpoint[0]
            url = endpoint[1]
            kwargs = endpoint[2] if len(endpoint) > 2 else {}   
            if "all" in include_set or filename.split(".")[0] in include_set:
                self.write_api_response(output_dir / filename, f'{repo_url}/{url}', **kwargs)

        logger.debug("Download completed successfully")

def load_saved_token(config_path: Path = CONFIG_PATH):
    """Load GitHub token from config file."""
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            return config.get("token")
        except json.JSONDecodeError:
            return None
    return None

def save_token(token, config_path: Path = CONFIG_PATH):
    """Save GitHub token to config file with restricted permissions."""
    print(f"Saving token to {config_path}")
    
    # Create config.json with restricted permissions
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.parent.chmod(0o700)
    config_path.touch(exist_ok=True)
    config_path.chmod(0o600)
    
    # Read existing config if present
    config = {}
    if config_path.stat().st_size > 0:
        try:
            config = json.loads(config_path.read_text())
            if not isinstance(config, dict):
                raise ValueError("Config file must contain a JSON object")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")
    
    # Update token and write back
    config["token"] = token
    config_path.write_text(json.dumps(config, indent=2))

def get_token(token: Optional[str], no_login: bool = False, config_path: Path = CONFIG_PATH, interactive: bool = False):
    """Apply our logic for choosing a github token, in order of preference."""
    # --no-login takes precedence over --token
    if no_login:
        return None
    # --token flag
    if token:
        return token
    # GITHUB_TOKEN env var
    if token := os.environ.get('GITHUB_TOKEN'):
        return token
    # config.json
    if token := load_saved_token(config_path):
        return token
    # interactive login
    if interactive:
        token = github_auth_device()
        save_token(token)
        return token
    # no token found
    return None


@click.group()
def cli():
    """GitHub repository downloader and utility tool."""
    pass

@cli.command()
@click.argument('repo')
@click.option('--no-login', is_flag=True, help='Download without authentication')
@click.option('--token', help='GitHub API token')
@click.option('--output', '-o', help='Output directory', type=click.Path(path_type=Path))
@click.option('--include', 
              help='Comma-separated list of elements to include: ' + ', '.join(valid_include_items))
@click.option('--log-level', 
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
              default='INFO',
              help='Set logging level')
def download(repo, no_login, token, output, include, log_level):
    """Download a GitHub repository and its metadata."""
    # Move existing main() logic here
    # Configure logging - set root logger level to affect all loggers
    level = getattr(logging, log_level.upper())
    logging.getLogger().setLevel(level)
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    token = get_token(token, no_login, interactive=True)
    logger.debug(f"Using {'unauthenticated' if not token else 'authenticated'} access")

    # Parse include options
    if include:
        include_items = [opt.strip().lower() for opt in include.split(",")]
        unknown_items = set(include_items) - set(valid_include_items)
        if unknown_items:
            logger.error(f"Unknown include items: {', '.join(unknown_items)}")
            return
    else:
        include_items = ["all"]

    owner, repo_name = repo.split("/")

    logger.debug("Starting GitHub repository download")

    downloader = Downloader(owner, repo_name, token)
    downloader.download_repo(output, include_items)

@cli.command()
@click.option('--no-login', is_flag=True, help='Check rate limit without authentication')
@click.option('--token', help='GitHub API token')
def rate_limit(no_login, token):
    """Show current GitHub API rate limit status."""
    token = get_token(token, no_login, interactive=False)
    api = GitHubAPI(token)
    limits = api.request('rate_limit').json()
    
    # Print rate limits in a readable format
    for category, data in limits['resources'].items():
        reset_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data['reset']))
        print(f"\n{category.upper()}:")
        print(f"  Limit: {data['limit']}")
        print(f"  Used: {data['used']}")
        print(f"  Remaining: {data['remaining']}")
        print(f"  Resets at: {reset_time}")

@cli.command()
@click.option('--save', is_flag=True, help='Save token to config file')
def auth(save):
    """Authenticate with GitHub and get an access token."""
    token = github_auth_device()
    print(f"\nReceived token: {token}")
    
    if save:
        save_token(token)
        print(f"Token saved to {CONFIG_PATH}")

if __name__ == "__main__":
    cli()
