"""Portable Tianzheng basic-data reader; no proprietary runtime dependencies."""
from .api import read_native_entity, native_geometry_facts
from .reader import read_document, is_native_entity

BASELINE_VERSION='0.5.1-native-fields-inline'
