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


logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".gitspoke" / "config.json"

class GitHubAPI:
    BASE_URL = "https://api.github.com/"
    
    def __init__(self, token: Optional[str] = None):
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
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def paginate(self, path: str, method: str = 'GET', list_key: str | None = None, **kwargs) -> Iterator[Any]:
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
        
        while True:
            response = self.request(url, **kwargs)
            items = response.json()
            if list_key:
                items = items[list_key]
            yield from items
            
            if 'next' not in response.links:
                break
            url = response.links['next']['url']

class Downloader:
    def __init__(self, url: str, token: Optional[str] = None):
        self.token = token
        # Parse url
        match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
        if not match:
            raise ValueError(f"Invalid GitHub URL: {url}")
        self.owner, self.repo_name = match.groups()
        self.repo_name = self.repo_name.rstrip('.git')

        self.api = GitHubAPI(token)

    def write_api_response(self, path: Path, endpoint: str, paginate: bool = True, **kwargs):
        """Write API response to a JSON file. By default paginates through all results."""
        if path.exists():
            logger.info(f"Skipping {path.name} - file already exists")
            return
            
        logger.info(f"Downloading {path.name}...")
        
        if paginate:
            results = list(self.api.paginate(endpoint, **kwargs))
        else:
            response = self.api.request(endpoint, **kwargs)
            results = response.json()
        
        path.write_text(json.dumps(results, indent=2))
        
        if paginate:
            logger.debug(f"Wrote {len(results)} items to {path}")
        else:
            logger.debug(f"Wrote response to {path}")

    def download_git_repo(self, output_dir: Path):
        """Download complete git repository using git protocol."""
        bundle_file = output_dir / f"git.bundle"
        
        if bundle_file.exists():
            logger.info("Git bundle already exists, skipping...")
            return

        logger.info("Downloading complete git repository...")

        # Extract the HTTPS clone URL
        if self.token:
            clone_url = f"https://oauth2:{self.token}@github.com/{self.owner}/{self.repo_name}.git"
        else:
            clone_url = f"https://github.com/{self.owner}/{self.repo_name}.git"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run([
                "git", "clone", "--mirror", clone_url, str(temp_dir)
            ], check=True)
            subprocess.run([
                "git", "bundle", "create", str(bundle_file), "--all"
            ], cwd=str(temp_dir), check=True)

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

    def download_repo(self, output_dir: Optional[Path] = None):
        """Download repository metadata and git content."""
        # Check if repo exists
        logger.debug(f"Checking if repository {self.owner}/{self.repo_name} exists")
        repo_url = f'/repos/{self.owner}/{self.repo_name}'
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

        # Download the complete git repository
        self.download_git_repo(output_dir)

        # Save repository info
        (output_dir / "repo_info.json").write_text(json.dumps(repo_info, indent=2))

        # Download readme
        self.download_readme(output_dir)

        # Download various repository data
        endpoints = [
            ("issues.json", "issues?state=all"),
            ("issue_comments.json", "issues/comments"),
            ("pull_requests.json", "pulls?state=all"),
            ("pr_review_comments.json", "pulls/comments"),
            ("releases.json", "releases"),
            ("stargazers.json", "stargazers"),
            ("watchers.json", "subscribers"),
            ("contributors.json", "contributors"),
            ("commit_comments.json", "comments"),
            ("labels.json", "labels"),
            ("milestones.json", "milestones?state=all"),
            ("forks.json", "forks"),
            ("branches.json", "branches"),
            ("tags.json", "tags"),
            ("security_advisories.json", "security-advisories"),
        ]

        # Handle standard endpoints
        for filename, endpoint in endpoints:
            self.write_api_response(output_dir / filename, f'{repo_url}/{endpoint}')

        # special cases
        self.write_api_response(
            output_dir / "workflows.json",
            f'{repo_url}/actions/workflows',
            list_key='workflows',
        )
        self.write_api_response(
            output_dir / "languages.json",
            f'{repo_url}/languages',
            paginate=False  # languages endpoint returns a dict
        )

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

@click.command()
@click.argument('url')
@click.option('--no-login', is_flag=True, help='Download without authentication')
@click.option('--token', envvar='GITHUB_TOKEN', help='GitHub API token')
@click.option('--output', '-o', help='Output directory', type=click.Path(path_type=Path))
@click.option('--log-level', 
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
              default='INFO',
              help='Set logging level')
def main(url, no_login, token, output, log_level):
    # Configure logging - set root logger level to affect all loggers
    level = getattr(logging, log_level.upper())
    logging.getLogger().setLevel(level)
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger.debug("Starting GitHub repository download")

    if not no_login and not token:
        token = os.environ.get('GITHUB_TOKEN') or load_saved_token()
        if not token:
            logger.info("No token found, starting device authentication flow...")
            token = github_auth_device()
            save_token(token)
            logger.debug("Successfully saved new token")

    downloader = Downloader(url, token)
    downloader.download_repo(output)

if __name__ == "__main__":
    main()
