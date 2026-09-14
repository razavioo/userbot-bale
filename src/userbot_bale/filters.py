"""Composable filters for Bale userbot events, inspired by Telethon and Pyrogram."""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable, Coroutine, Iterable, Pattern, Sequence, Union


class Filter:
    """Base class for all message and event filters.

    Supports composition via bitwise operators:
        `filter_a & filter_b` (AND)
        `filter_a | filter_b` (OR)
        `~filter_a`           (NOT)
    """

    async def check(self, client: Any, event: Any) -> bool:
        """Evaluate the filter asynchronously."""
        res = self(client, event)
        if inspect.isawaitable(res):
            return bool(await res)
        return bool(res)

    def __call__(self, client: Any, event: Any) -> Union[bool, Coroutine[Any, Any, bool]]:
        """Evaluate the filter synchronously if supported, or return a coroutine."""
        return True

    def __and__(self, other: "Filter") -> "AndFilter":
        return AndFilter(self, other)

    def __or__(self, other: "Filter") -> "OrFilter":
        return OrFilter(self, other)

    def __invert__(self) -> "InvertFilter":
        return InvertFilter(self)


class AndFilter(Filter):
    def __init__(self, *filters: Filter) -> None:
        self.filters = filters

    async def check(self, client: Any, event: Any) -> bool:
        for f in self.filters:
            if not await f.check(client, event):
                return False
        return True

    def __call__(self, client: Any, event: Any) -> bool:
        for f in self.filters:
            res = f(client, event)
            if inspect.isawaitable(res):
                # When any subfilter is async, check() must be awaited
                raise RuntimeError("Async filter used in sync check; await check() instead")
            if not res:
                return False
        return True

    def __repr__(self) -> str:
        return f"({' & '.join(repr(f) for f in self.filters)})"


class OrFilter(Filter):
    def __init__(self, *filters: Filter) -> None:
        self.filters = filters

    async def check(self, client: Any, event: Any) -> bool:
        for f in self.filters:
            if await f.check(client, event):
                return True
        return False

    def __call__(self, client: Any, event: Any) -> bool:
        for f in self.filters:
            res = f(client, event)
            if inspect.isawaitable(res):
                raise RuntimeError("Async filter used in sync check; await check() instead")
            if res:
                return True
        return False

    def __repr__(self) -> str:
        return f"({' | '.join(repr(f) for f in self.filters)})"


class InvertFilter(Filter):
    def __init__(self, target: Filter) -> None:
        self.target = target

    async def check(self, client: Any, event: Any) -> bool:
        return not await self.target.check(client, event)

    def __call__(self, client: Any, event: Any) -> bool:
        res = self.target(client, event)
        if inspect.isawaitable(res):
            raise RuntimeError("Async filter used in sync check; await check() instead")
        return not res

    def __repr__(self) -> str:
        return f"(~{repr(self.target)})"


class AllFilter(Filter):
    def __call__(self, client: Any, event: Any) -> bool:
        return True

    def __repr__(self) -> str:
        return "filters.all"


class TextFilter(Filter):
    def __call__(self, client: Any, event: Any) -> bool:
        text = getattr(event, "text", None)
        return bool(text and isinstance(text, str) and text.strip())

    def __repr__(self) -> str:
        return "filters.text"


class CommandFilter(Filter):
    def __init__(
        self,
        commands: Union[str, Sequence[str]],
        prefixes: Union[str, Sequence[str]] = "/",
        case_sensitive: bool = False,
    ) -> None:
        if isinstance(commands, str):
            self.commands = [commands if case_sensitive else commands.lower()]
        else:
            self.commands = [c if case_sensitive else c.lower() for c in commands]
        self.prefixes = tuple(prefixes) if isinstance(prefixes, (list, tuple, set)) else (prefixes,)
        self.case_sensitive = case_sensitive

    def __call__(self, client: Any, event: Any) -> bool:
        raw_text = getattr(event, "text", "") or ""
        text = raw_text.strip()
        if not text:
            return False

        matching_prefix = None
        for p in self.prefixes:
            if text.startswith(p):
                matching_prefix = p
                break
        if matching_prefix is None:
            return False

        without_prefix = text[len(matching_prefix):].strip()
        parts = without_prefix.split()
        if not parts:
            return False

        cmd = parts[0] if self.case_sensitive else parts[0].lower()
        if cmd in self.commands:
            event.command = cmd
            event.args = parts[1:]
            return True
        return False

    def __repr__(self) -> str:
        return f"filters.command({self.commands!r}, prefixes={self.prefixes!r})"


class RegexFilter(Filter):
    def __init__(self, pattern: Union[str, Pattern[str]], flags: int = 0) -> None:
        if isinstance(pattern, str):
            self.pattern = re.compile(pattern, flags)
        else:
            self.pattern = pattern

    def __call__(self, client: Any, event: Any) -> bool:
        text = getattr(event, "text", "") or ""
        match = self.pattern.search(text)
        if match:
            event.pattern_match = match
            return True
        return False

    def __repr__(self) -> str:
        return f"filters.regex({self.pattern.pattern!r})"


class PeerFilter(Filter):
    def __init__(self, peers: Union[int, Iterable[int]]) -> None:
        if isinstance(peers, int):
            self.peers = {peers}
        else:
            self.peers = set(peers)

    def __call__(self, client: Any, event: Any) -> bool:
        peer_id = getattr(event, "peer_id", None)
        return peer_id in self.peers

    def __repr__(self) -> str:
        return f"filters.peer({self.peers!r})"


class SenderFilter(Filter):
    def __init__(self, senders: Union[int, Iterable[int]]) -> None:
        if isinstance(senders, int):
            self.senders = {senders}
        else:
            self.senders = set(senders)

    def __call__(self, client: Any, event: Any) -> bool:
        sender_id = getattr(event, "sender_id", None)
        return sender_id in self.senders

    def __repr__(self) -> str:
        return f"filters.sender({self.senders!r})"


class PrivateFilter(Filter):
    def __call__(self, client: Any, event: Any) -> bool:
        return getattr(event, "peer_type", 1) == 1

    def __repr__(self) -> str:
        return "filters.private"


class GroupFilter(Filter):
    def __call__(self, client: Any, event: Any) -> bool:
        return getattr(event, "peer_type", 1) == 2

    def __repr__(self) -> str:
        return "filters.group"


class ChannelFilter(Filter):
    def __call__(self, client: Any, event: Any) -> bool:
        return getattr(event, "peer_type", 1) == 3

    def __repr__(self) -> str:
        return "filters.channel"


class IncomingFilter(Filter):
    def __call__(self, client: Any, event: Any) -> bool:
        my_id = getattr(client, "user_id", None)
        sender_id = getattr(event, "sender_id", None)
        if my_id and sender_id and my_id == sender_id:
            return False
        return getattr(event, "direction", "inbound") != "outbound"

    def __repr__(self) -> str:
        return "filters.incoming"


class OutgoingFilter(Filter):
    def __call__(self, client: Any, event: Any) -> bool:
        my_id = getattr(client, "user_id", None)
        sender_id = getattr(event, "sender_id", None)
        if my_id and sender_id and my_id == sender_id:
            return True
        return getattr(event, "direction", "inbound") == "outbound"

    def __repr__(self) -> str:
        return "filters.outgoing"


class MeFilter(Filter):
    def __call__(self, client: Any, event: Any) -> bool:
        my_id = getattr(client, "user_id", None)
        sender_id = getattr(event, "sender_id", None)
        return bool(my_id and sender_id and my_id == sender_id)

    def __repr__(self) -> str:
        return "filters.me"


class CustomFilter(Filter):
    def __init__(self, func: Callable[..., Any], name: str | None = None) -> None:
        self.func = func
        self.name = name or getattr(func, "__name__", "custom_filter")
        sig = inspect.signature(func)
        self._param_count = len(sig.parameters)

    async def check(self, client: Any, event: Any) -> bool:
        if self._param_count == 1:
            res = self.func(event)
        else:
            res = self.func(client, event)
        if inspect.isawaitable(res):
            return bool(await res)
        return bool(res)

    def __call__(self, client: Any, event: Any) -> Union[bool, Coroutine[Any, Any, bool]]:
        if self._param_count == 1:
            return self.func(event)
        return self.func(client, event)

    def __repr__(self) -> str:
        return f"filters.create({self.name})"


# Module-level instances and factories
all = AllFilter()
text = TextFilter()
private = PrivateFilter()
group = GroupFilter()
channel = ChannelFilter()
incoming = IncomingFilter()
outgoing = OutgoingFilter()
me = MeFilter()


def command(
    commands: Union[str, Sequence[str]],
    prefixes: Union[str, Sequence[str]] = "/",
    case_sensitive: bool = False,
) -> CommandFilter:
    return CommandFilter(commands, prefixes=prefixes, case_sensitive=case_sensitive)


def regex(pattern: Union[str, Pattern[str]], flags: int = 0) -> RegexFilter:
    return RegexFilter(pattern, flags=flags)


def peer(peers: Union[int, Iterable[int]]) -> PeerFilter:
    return PeerFilter(peers)


def sender(senders: Union[int, Iterable[int]]) -> SenderFilter:
    return SenderFilter(senders)


def create(func: Callable[..., Any], name: str | None = None) -> CustomFilter:
    return CustomFilter(func, name=name)
