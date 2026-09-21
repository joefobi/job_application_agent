from job_application_agent import __version__


def test_package_version() -> None:
    """Verify the package exposes a version string."""
    assert __version__ == "0.1.0"
