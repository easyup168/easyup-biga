"""Local CSV adjustment-factor adapter identity."""
SOURCE_PREFIX = "csv-adjustment:"
from easyup_biga.data.datasets.adjustment_factors import CsvAdjustmentFactorProvider
__all__ = ["CsvAdjustmentFactorProvider", "SOURCE_PREFIX"]
