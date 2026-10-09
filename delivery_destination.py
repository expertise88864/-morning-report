"""Strict GitHub HTTPS identity comparison for the local publication gate."""
from urllib.parse import urlsplit


def matches_repository(url: str, repository: str) -> bool:
    try:
        destination = urlsplit(url)
    except ValueError:
        return False
    path = destination.path.removesuffix("/").removesuffix(".git")
    return (destination.scheme == "https" and destination.netloc.casefold() == "github.com"
            and not destination.query and not destination.fragment
            and path.casefold() == "/" + repository.casefold())
