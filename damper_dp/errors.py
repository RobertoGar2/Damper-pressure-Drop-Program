"""Exceptions shared by the damper pressure-drop modules."""


class DataError(Exception):
    """A problem with a model's data files or workbook."""


class ModelError(Exception):
    """The model cannot produce an estimate for the request."""
