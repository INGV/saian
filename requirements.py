from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, FormatStrFormatter, AutoMinorLocator
from obspy import Stream, Trace, UTCDateTime, read_inventory
from obspy.clients.fdsn import Client
from obspy.core.inventory import Inventory
from obspy.geodetics import locations2degrees, kilometers2degrees
from pyrocko import cake
