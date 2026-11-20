import os

# The suite never calls the PyPI / npm registries: licence lookups read the cache only. Tests that
# exercise resolution inject a fetcher (tests/coded_tools/test_p7.py).
os.environ.setdefault("SENTINEL_LICENSE_LOOKUP", "off")
