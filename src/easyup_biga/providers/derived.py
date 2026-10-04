"""Identity module for BigA-local derived datasets.

Derived datasets have no external network adapter, but they still participate in the
same dataset/provider lineage.  The source prefix is explicit so provenance never
pretends a derived row came from a market-data vendor.
"""
SOURCE_PREFIX = "derived:"
