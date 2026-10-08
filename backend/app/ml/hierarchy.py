"""Grouped hierarchy: Global -> Home Region -> OEM -> Product, plus OEM-total and Product-total aggregates."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ALL = "ALL"
LEVELS = ["TOTAL", "REGION", "OEM", "PRODUCT", "OEM_REGION", "BOTTOM"]


def node_id(oem: str, region: str, product: str) -> str:
    return f"{oem}|{region}|{product}"


def level_of(oem: str, region: str, product: str) -> str:
    key = (oem != ALL, region != ALL, product != ALL)
    return {(False, False, False): "TOTAL", (False, True, False): "REGION", (True, False, False): "OEM", (False, False, True): "PRODUCT",
            (True, True, False): "OEM_REGION", (True, True, True): "BOTTOM"}.get(key, "INVALID")


@dataclass
class Hierarchy:
    bottoms: list[tuple[str, str, str]]
    nodes: list[tuple[str, str, str]]  # rows of S
    S: np.ndarray  # (n_nodes, n_bottom)
    tags: dict[str, np.ndarray]  # level -> row indices (hierarchicalforecast format)

    @property
    def ids(self) -> list[str]:
        return [node_id(*n) for n in self.nodes]

    @property
    def n_bottom(self) -> int:
        return len(self.bottoms)

    def levels(self) -> list[str]:
        return [level_of(*n) for n in self.nodes]

    def index(self) -> dict[str, int]:
        return {i: k for k, i in enumerate(self.ids)}

    def leaves_of(self, oem: str, region: str, product: str) -> np.ndarray:
        """Boolean mask over bottoms belonging to a (possibly aggregate) node."""
        return np.array([(oem in (ALL, b[0])) and (region in (ALL, b[1])) and (product in (ALL, b[2])) for b in self.bottoms])


def build_hierarchy(bottoms: list[tuple[str, str, str]]) -> Hierarchy:
    bottoms = sorted(set(bottoms))
    oems = sorted({b[0] for b in bottoms})
    regs = sorted({b[1] for b in bottoms})
    prods = sorted({b[2] for b in bottoms})
    nodes: list[tuple[str, str, str]] = [(ALL, ALL, ALL)]
    nodes += [(ALL, r, ALL) for r in regs]
    nodes += [(o, ALL, ALL) for o in oems]
    nodes += [(ALL, ALL, p) for p in prods]
    nodes += [(o, r, ALL) for o in oems for r in regs if any(b[0] == o and b[1] == r for b in bottoms)]
    nodes += bottoms
    S = np.zeros((len(nodes), len(bottoms)))
    for i, (o, r, p) in enumerate(nodes):
        for j, b in enumerate(bottoms):
            S[i, j] = float((o in (ALL, b[0])) and (r in (ALL, b[1])) and (p in (ALL, b[2])))
    lv = [level_of(*n) for n in nodes]
    tags = {name: np.array([i for i, x in enumerate(lv) if x == name]) for name in LEVELS if name in lv}
    return Hierarchy(bottoms, nodes, S, tags)


def aggregate_wide(bottom_wide: pd.DataFrame, h: Hierarchy) -> pd.DataFrame:
    """bottom_wide: index=ds, columns=node_id(bottom) -> wide frame for all nodes (index=ds, columns=node ids)."""
    cols = [node_id(*b) for b in h.bottoms]
    arr = bottom_wide[cols].to_numpy() @ h.S.T
    return pd.DataFrame(arr, index=bottom_wide.index, columns=h.ids)
