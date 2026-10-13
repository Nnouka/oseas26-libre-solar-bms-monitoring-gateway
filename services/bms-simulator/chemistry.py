"""Cell chemistry: how the open-circuit voltage (OCV) of one LiFePO4 cell depends on its charge.

The numbers are copied from `ocv_lfp` in the Libre Solar firmware (app/src/bms_common.c), where they
are listed from 100 % down to 0 %.  Here they are listed from 0 % up, because that is the natural
order for a lookup table.  Both the physical battery (as the truth) and the firmware (as its
estimate) use them.
"""
from __future__ import annotations

SOC_PCT = [5.0 * i for i in range(21)]                      # 0, 5, 10 ... 100
OCV_V = [2.833, 3.132, 3.177, 3.190, 3.213, 3.226, 3.240, 3.252, 3.262, 3.264, 3.265,
         3.268, 3.271, 3.275, 3.283, 3.296, 3.304, 3.308, 3.309, 3.314, 3.392]


def interp(x: float, xs: list[float], ys: list[float]) -> float:
    """Linear interpolation of y at x, given sample points (xs, ys) with xs increasing.

    Outside the table the first / last y value is returned (no extrapolation).
    """
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            fraction = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + fraction * (ys[i] - ys[i - 1])
    return ys[-1]          # unreachable; keeps type checkers quiet
