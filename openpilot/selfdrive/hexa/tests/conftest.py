import os

# The canal's tests were written for the Haval build: they run as that edition (selfdrive/hexa/edition.py).
# The generic edition has its own test, test_edition.py, in a separate process.
os.environ["LIZPILOT_EDITION"] = "h6gt"
