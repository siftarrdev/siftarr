"""Async helper utilities shared by sync flows."""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Literal, overload


@overload
async def gather_limited[T, R](
    items: Iterable[T],
    limit: int,
    worker: Callable[[T], Awaitable[R]],
    *,
    return_exceptions: Literal[False] = False,
) -> list[R]: ...


@overload
async def gather_limited[T, R](
    items: Iterable[T],
    limit: int,
    worker: Callable[[T], Awaitable[R]],
    *,
    return_exceptions: Literal[True],
) -> list[R | BaseException]: ...


async def gather_limited[T, R](
    items: Iterable[T],
    limit: int,
    worker: Callable[[T], Awaitable[R]],
    *,
    return_exceptions: bool = False,
) -> list[R] | list[R | BaseException]:
    """Run async work with a bounded level of concurrency."""
    semaphore = asyncio.Semaphore(max(1, limit))

    async def run(item: T) -> R:
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(
        *(run(item) for item in items),
        return_exceptions=return_exceptions,
    )
