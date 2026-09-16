"""Conservative enemy-cluster and landing-zone projection for SMB1.

This module intentionally does not pretend to be an exact ballistic model.
It projects structured enemy radar and conservative terrain validity into a
small, auditable heuristic used by the live planner.

Important boundary:

- enemy occupancy is one landing hazard signal;
- terrain support is a separate SAFE / GAP / UNKNOWN signal;
- UNKNOWN never silently becomes SAFE;
- exact Mesen rollout remains the final trajectory authority.
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_CLUSTER_GAP_PX = 48
DEFAULT_LANDING_NEAR_PX = 96
DEFAULT_LANDING_FAR_PX = 160

TERRAIN_SAFE = "SAFE"
TERRAIN_GAP = "GAP"
TERRAIN_UNKNOWN = "UNKNOWN"

LANDING_SAFE = "SAFE"
LANDING_GAP = "GAP"
LANDING_UNKNOWN = "UNKNOWN"
LANDING_ENEMY = "UNSAFE_ENEMY"


@dataclass(frozen=True)
class EnemyCluster:
    start_dx: int
    end_dx: int
    count: int
    enemy_dxs: tuple[int, ...]

    @property
    def span_px(self) -> int:
        return self.end_dx - self.start_dx


@dataclass(frozen=True)
class LandingZoneAssessment:
    forward_enemy_count: int
    clusters: tuple[EnemyCluster, ...]
    nearest_cluster: EnemyCluster | None
    landing_near_px: int
    landing_far_px: int
    landing_enemy_count: int
    landing_enemy_dxs: tuple[int, ...]
    terrain_status: str
    terrain_sample_count: int
    terrain_valid_sample_count: int
    terrain_gap_dxs: tuple[int, ...]
    terrain_unknown_dxs: tuple[int, ...]

    @property
    def landing_enemy_clear(self) -> bool:
        return self.landing_enemy_count == 0

    @property
    def landing_enemy_unsafe(self) -> bool:
        return self.landing_enemy_count > 0

    @property
    def landing_status(self) -> str:
        if self.landing_enemy_unsafe:
            return LANDING_ENEMY
        if self.terrain_status == TERRAIN_GAP:
            return LANDING_GAP
        if self.terrain_status == TERRAIN_SAFE:
            return LANDING_SAFE
        return LANDING_UNKNOWN

    @property
    def landing_safe(self) -> bool:
        """True only when both enemy occupancy and terrain support are known safe."""
        return self.landing_status == LANDING_SAFE

    @property
    def landing_unsafe(self) -> bool:
        """Legacy policy signal: enemy occupancy only.

        V20/V26 historically use this property to choose the enemy-cluster jump
        macro. Terrain GAP/UNKNOWN must not silently repurpose that macro, so the
        backward-compatible property remains enemy-specific. Use
        ``landing_known_unsafe`` or ``landing_status`` for full semantics.
        """
        return self.landing_enemy_unsafe

    @property
    def landing_known_unsafe(self) -> bool:
        return self.landing_status in {LANDING_ENEMY, LANDING_GAP}

    @property
    def landing_unknown(self) -> bool:
        return self.landing_status == LANDING_UNKNOWN

    def to_payload(self) -> dict:
        nearest = self.nearest_cluster
        return {
            "forward_enemy_count": self.forward_enemy_count,
            "enemy_cluster_count": len(self.clusters),
            "nearest_cluster_count": 0 if nearest is None else nearest.count,
            "nearest_cluster_start_dx": None if nearest is None else nearest.start_dx,
            "nearest_cluster_end_dx": None if nearest is None else nearest.end_dx,
            "nearest_cluster_span_px": None if nearest is None else nearest.span_px,
            "landing_corridor_start_dx": self.landing_near_px,
            "landing_corridor_end_dx": self.landing_far_px,
            "landing_enemy_count": self.landing_enemy_count,
            "landing_enemy_dxs": list(self.landing_enemy_dxs),
            "landing_enemy_clear": self.landing_enemy_clear,
            "landing_enemy_unsafe": self.landing_enemy_unsafe,
            "landing_terrain_status": self.terrain_status,
            "landing_terrain_sample_count": self.terrain_sample_count,
            "landing_terrain_valid_sample_count": self.terrain_valid_sample_count,
            "landing_terrain_gap_dxs": list(self.terrain_gap_dxs),
            "landing_terrain_unknown_dxs": list(self.terrain_unknown_dxs),
            "landing_status": self.landing_status,
            "landing_safe": self.landing_safe,
            "landing_unsafe": self.landing_unsafe,
            "landing_known_unsafe": self.landing_known_unsafe,
            "landing_unknown": self.landing_unknown,
        }


def _forward_enemy_dxs(radar: dict) -> tuple[int, ...]:
    values: list[int] = []
    for enemy in radar.get("enemies") or ():
        try:
            dx = int(enemy.get("dx"))
        except (AttributeError, TypeError, ValueError):
            continue
        if dx >= 0:
            values.append(dx)
    return tuple(sorted(values))


def cluster_forward_enemies(
    radar: dict,
    *,
    cluster_gap_px: int = DEFAULT_CLUSTER_GAP_PX,
) -> tuple[EnemyCluster, ...]:
    """Group forward enemies whose adjacent separation is within one gap bound."""
    if cluster_gap_px < 0:
        raise ValueError("cluster_gap_px must be >= 0")

    dxs = _forward_enemy_dxs(radar)
    if not dxs:
        return ()

    groups: list[list[int]] = [[dxs[0]]]
    for dx in dxs[1:]:
        if dx - groups[-1][-1] <= cluster_gap_px:
            groups[-1].append(dx)
        else:
            groups.append([dx])

    return tuple(
        EnemyCluster(
            start_dx=group[0],
            end_dx=group[-1],
            count=len(group),
            enemy_dxs=tuple(group),
        )
        for group in groups
    )


def _terrain_corridor_status(
    radar: dict,
    *,
    landing_near_px: int,
    landing_far_px: int,
) -> tuple[str, int, int, tuple[int, ...], tuple[int, ...]]:
    """Classify projected landing terrain as SAFE / GAP / UNKNOWN.

    Positive SAFE requires complete current coverage through the far edge of the
    landing corridor. A short lookahead or any explicitly UNKNOWN sampled column
    keeps the result UNKNOWN. Known empty current columns are GAP.
    """

    columns = []
    for column in radar.get("columns") or ():
        if not isinstance(column, dict):
            continue
        try:
            dx = int(column.get("dx"))
        except (TypeError, ValueError):
            continue
        if landing_near_px <= dx <= landing_far_px:
            columns.append((dx, column))

    try:
        lookahead_px = int(radar.get("lookahead_px", 0) or 0)
    except (TypeError, ValueError):
        lookahead_px = 0

    gap_dxs: list[int] = []
    unknown_dxs: list[int] = []
    valid_count = 0
    for dx, column in columns:
        validity = str(column.get("validity", "UNKNOWN")).upper()
        if validity != "CURRENT":
            unknown_dxs.append(dx)
            continue
        valid_count += 1
        if column.get("surface_row") is None:
            gap_dxs.append(dx)

    if gap_dxs:
        return TERRAIN_GAP, len(columns), valid_count, tuple(gap_dxs), tuple(unknown_dxs)

    if (
        lookahead_px >= landing_far_px
        and columns
        and valid_count == len(columns)
        and not unknown_dxs
    ):
        return TERRAIN_SAFE, len(columns), valid_count, (), ()

    return TERRAIN_UNKNOWN, len(columns), valid_count, (), tuple(unknown_dxs)


def assess_landing_zone(
    radar: dict,
    *,
    cluster_gap_px: int = DEFAULT_CLUSTER_GAP_PX,
    landing_near_px: int = DEFAULT_LANDING_NEAR_PX,
    landing_far_px: int = DEFAULT_LANDING_FAR_PX,
) -> LandingZoneAssessment:
    """Assess enemy occupancy and terrain validity in the landing corridor."""
    if landing_near_px < 0:
        raise ValueError("landing_near_px must be >= 0")
    if landing_far_px < landing_near_px:
        raise ValueError("landing_far_px must be >= landing_near_px")

    dxs = _forward_enemy_dxs(radar)
    clusters = cluster_forward_enemies(radar, cluster_gap_px=cluster_gap_px)
    landing = tuple(dx for dx in dxs if landing_near_px <= dx <= landing_far_px)
    terrain = _terrain_corridor_status(
        radar,
        landing_near_px=landing_near_px,
        landing_far_px=landing_far_px,
    )
    return LandingZoneAssessment(
        forward_enemy_count=len(dxs),
        clusters=clusters,
        nearest_cluster=None if not clusters else clusters[0],
        landing_near_px=int(landing_near_px),
        landing_far_px=int(landing_far_px),
        landing_enemy_count=len(landing),
        landing_enemy_dxs=landing,
        terrain_status=terrain[0],
        terrain_sample_count=terrain[1],
        terrain_valid_sample_count=terrain[2],
        terrain_gap_dxs=terrain[3],
        terrain_unknown_dxs=terrain[4],
    )
