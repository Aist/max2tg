"""conftest.py — add the project root to sys.path so 'app.*' imports resolve."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
