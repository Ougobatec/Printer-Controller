"""Filters applied to the live measurement (never to the values recorded by a program)."""

import statistics
from collections import deque

KINDS = ("none", "moving_average", "median", "exponential")


class Filter:
    def apply(self, value):
        return value

    def reset(self):
        pass


class MovingAverage(Filter):
    def __init__(self, size):
        self._values = deque(maxlen=max(1, int(size)))

    def apply(self, value):
        self._values.append(value)
        return sum(self._values) / len(self._values)

    def reset(self):
        self._values.clear()


class Median(MovingAverage):
    def apply(self, value):
        self._values.append(value)
        return statistics.median(self._values)


class Exponential(Filter):
    """y = y + alpha * (x - y), with alpha = 2 / (size + 1): about the same smoothing as a moving average."""

    def __init__(self, size):
        self._alpha = 2.0 / (max(1, int(size)) + 1)
        self._y = None

    def apply(self, value):
        self._y = value if self._y is None else self._y + self._alpha * (value - self._y)
        return self._y

    def reset(self):
        self._y = None


def make_filter(kind, size):
    if kind == "moving_average":
        return MovingAverage(size)
    if kind == "median":
        return Median(size)
    if kind == "exponential":
        return Exponential(size)
    return Filter()
