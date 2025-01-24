Gitspoke
========

Gitspoke is a tool for downloading complete archives of public GitHub repositories, including all metadata, issues, pull requests, and git history. The tool is intended for archival and analysis of open source projects.

Gitspoke downloads both the complete git repository and all available metadata through the GitHub API, storing everything in a structured directory format.

Gitspoke is not published by or associated with GitHub.

Quick start
------------

Download a repository without logging in:

```
gitspoke https://github.com/owner/repo --no-login
```

Download with GitHub authentication (recommended to avoid rate limits):

```
gitspoke https://github.com/owner/repo
```

Download to a specific directory:

```
gitspoke https://github.com/owner/repo -o /path/to/output
```

As a library:

```
from gitspoke import Downloader

downloader = Downloader(url, token)
downloader.download_repo(output_path)
```

Features
--------

Gitspoke downloads:

* Complete git repository history as a git bundle
* Repository metadata and settings
* Issues and issue comments
* Pull requests and review comments
* Releases and tags
* Stars and watchers
* Contributors and participation data
* Labels and milestones
* GitHub Actions workflows
* Security advisories
* Language statistics
* README in HTML format

Installation
------------

`gitspoke` is not yet available on PyPI, but can be installed from source:

```
pip install https://github.com/harvard-lil/gitspoke/archive/refs/heads/main.zip
```

Or installed as a tool by [uv](https://docs.astral.sh/uv/):

```
uv tool install --from git+https://github.com/harvard-lil/gitspoke gitspoke
```

Or run from [uvx](https://docs.astral.sh/uv/):

```
uvx --from git+https://github.com/harvard-lil/gitspoke gitspoke
```

Authentication
-------------

Gitspoke supports authentication in this order:

1. Anonymous access (with --no-login flag)
2. GitHub API token via `--token` option
3. GitHub API token via GITHUB_TOKEN environment variable
4. Saved token in `~/.gitspoke/config.json`
5. Device flow authentication (interactive login)

For best results, authenticate to avoid GitHub API rate limits. Gitspoke will automatically start the device flow authentication if no token is provided.

After using the device flow authentication, Gitspoke will save the token to a file in the user's home directory (`~/.gitspoke/config.json`). The token will be used automatically in future runs.

Output Format
------------

Gitspoke creates a directory structure containing:

* `git.bundle` - Complete git repository history
* `repo_info.json` - Basic repository metadata
* `readme.html` - Repository README in HTML format
* `issues.json`, `pull_requests.json`, etc. - results of GitHub API requests

The format is intended for later reading by programs, not necessarily for human consumption.
For example, issues and comments are kept in separate files that would have to be merged
for display.

Command Line Usage
-----------------

```
Usage: gitspoke [OPTIONS] URL

  Download complete GitHub repository archive

Options:
  --no-login               Download anonymously without authentication
  --token TEXT             GitHub API token
  -o, --output PATH        Output directory
  --log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]
                          Set logging level
  --help                   Show this message and exit
```

Development
-----------

Clone the repository and install dependencies:

```
git clone https://github.com/harvard-lil/gitspoke
cd gitspoke
uv run src/github_downloader/main.py
```

Current Limitations
------------------

* Gitspoke can download private repositories accessible by the token provided, but it currently
  focuses on API endpoints that work for unauthenticated users in public repositories.
* Gitspoke does not yet offer the ability to upload the downloaded data to a new GitHub repository
  or other source code hosting service.