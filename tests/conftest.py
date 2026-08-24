"""Shared test fixtures for index-scanner-mcp."""

import os
import pytest

TESTS_DIR = os.path.dirname(__file__)
TEST_SAMPLES_DIR = os.path.join(TESTS_DIR, "test_samples")


@pytest.fixture
def sample_java_path():
    return os.path.join(TEST_SAMPLES_DIR, "SampleEntity.java")


@pytest.fixture
def sample_python_path():
    return os.path.join(TEST_SAMPLES_DIR, "init_indexes.py")
