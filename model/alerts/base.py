"""Pluggable alert sink abstraction.

A channel just needs to accept a `DivergenceSignal` and side-effect it
somewhere — terminal, log file, webhook, Slack, Telegram, etc. The
streaming poller will fan-out a single signal to every registered channel.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..divergence.types import DivergenceSignal


class AlertChannel(ABC):
	@abstractmethod
	def send(self, signal: DivergenceSignal) -> None:
		"""Deliver `signal` synchronously. MUST NOT raise on transient errors."""
		raise NotImplementedError
