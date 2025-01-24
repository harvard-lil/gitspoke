from importlib.metadata import version, PackageNotFoundError
from .cli import Downloader, GitHubAPI


try:
    __version__ = version("gitspoke")
except PackageNotFoundError: # pragma: no cover
    # package is not installed
    __version__ = "0.0.0.dev0"

__all__ = ["Downloader", "GitHubAPI"]
