"""Compatibility imports for applications using the former canable25 name."""
import importlib as _importlib
import sys as _sys
from canable import *
from canable import __all__, __version__

# Alias every implementation module so classes and exceptions have one identity.
for _name in ("models", "errors", "protocol", "interfaces", "winusb", "device", "channel", "connection"):
    _module = _importlib.import_module("canable." + _name)
    _sys.modules[__name__ + "." + _name] = _module
    globals()[_name] = _module
del _name, _module, _importlib, _sys
