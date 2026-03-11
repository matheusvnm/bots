from pathlib import Path
from typing import Any

from loguru import logger
from patchright.sync_api import Page

from components.dtos import KrakenCredentials
from components.trace import PageTracer

from .deposit import KrakenDeposit
from .login import KrakenAuthenticator
from .withdraw import KrakenWithdraw
