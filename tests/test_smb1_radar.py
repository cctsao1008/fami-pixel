from fami_pixel.games.smb1.radar import (
    ADDR_ENEMY_FLAG,
    ADDR_ENEMY_ID,
    ADDR_ENEMY_PAGE,
    ADDR_ENEMY_STATE,
    ADDR_ENEMY_X,
    ADDR_ENEMY_Y,
    ADDR_ENEMY_Y_HIGH,
    ADDR_PLAYER_STATUS,
    ADDR_POWER_UP_TYPE,
    ADDR_SCREEN_RIGHT_PAGE,
    ADDR_SCREEN_RIGHT_X,
    ADDR_STAR_INVINCIBLE_TIMER,
    BLOCK_BUFFER_1,
    BLOCK_BUFFER_2,
    POWER_UP_OBJECT_ID,
    POWER_UP_SLOT,
    TERRAIN_CURRENT,
    TERRAIN_UNKNOWN,
    decode_smb1_radar,
)


def _block_addr(world_x: int, row: int) -> int:
    column = (world_x >> 4) & 0x1F
    base = BLOCK_BUFFER_1 if column < 16 else BLOCK_BUFFER_2
    return base + (column & 0x0F) + (row << 4)


def _ground(ram: bytearray, world_x: int, row: int = 11, metatile: int = 0x54):
    ram[_block_addr(world_x, row)] = metatile


def test_radar_reports_enemy_gap_and_raised_obstacle_ahead():
    ram = bytearray(0x800)
    player_x = 100
    ram[ADDR_SCREEN_RIGHT_PAGE] = 0x01
    ram[ADDR_SCREEN_RIGHT_X] = 0x20  # world X 288

    # Current floor and the next two normal ground columns.
    _ground(ram, player_x, row=11)
    _ground(ram, 112, row=11)
    _ground(ram, 128, row=11)
    # Raised terrain/pipe-like collision column, then a gap at X=160.
    _ground(ram, 144, row=9)

    ram[ADDR_ENEMY_FLAG] = 1
    ram[ADDR_ENEMY_ID] = 0x06
    ram[ADDR_ENEMY_STATE] = 0
    ram[ADDR_ENEMY_PAGE] = 0
    ram[ADDR_ENEMY_X] = 150
    ram[ADDR_ENEMY_Y_HIGH] = 1
    ram[ADDR_ENEMY_Y] = 176

    radar = decode_smb1_radar(bytes(ram), player_x=player_x, lookahead_px=192)

    assert radar.nearest_enemy_dx == 50
    assert radar.nearest_obstacle_dx == 44
    assert radar.nearest_gap_dx == 60
    assert radar.hazard_ahead is True
    assert radar.enemies[0].enemy_id == 0x06
    assert radar.to_payload()["nearest_enemy_dx"] == 50


def test_radar_marks_loader_margin_unknown_and_does_not_call_it_a_gap():
    ram = bytearray(0x800)
    player_x = 100
    ram[ADDR_SCREEN_RIGHT_PAGE] = 0x00
    ram[ADDR_SCREEN_RIGHT_X] = 128

    _ground(ram, player_x, row=11)
    _ground(ram, 112, row=11)
    _ground(ram, 128, row=11)
    # X=144 is the retained one-tile loader margin. Leave its rolling-buffer
    # bytes zero: because it is beyond screen_right it must be UNKNOWN, not GAP.

    radar = decode_smb1_radar(bytes(ram), player_x=player_x, lookahead_px=64)
    payload = radar.to_payload()

    by_x = {column.world_x: column for column in radar.columns}
    assert by_x[112].validity == TERRAIN_CURRENT
    assert by_x[128].validity == TERRAIN_CURRENT
    assert by_x[144].validity == TERRAIN_UNKNOWN
    assert by_x[144].known_gap is False
    assert radar.nearest_gap_dx is None
    assert radar.terrain_valid_through_x == 128

    col144 = next(column for column in payload["columns"] if column["x"] == 144)
    assert col144["validity"] == "UNKNOWN"
    assert len(col144["collision_samples"]) == 5
    assert all("address" in sample and "value" in sample for sample in col144["collision_samples"])


def test_radar_column_records_surface_address_and_value():
    ram = bytearray(0x800)
    player_x = 100
    ram[ADDR_SCREEN_RIGHT_PAGE] = 0x00
    ram[ADDR_SCREEN_RIGHT_X] = 160
    _ground(ram, player_x, row=11)
    _ground(ram, 112, row=9, metatile=0x66)

    radar = decode_smb1_radar(bytes(ram), player_x=player_x, lookahead_px=32)
    column = next(column for column in radar.columns if column.world_x == 112)

    assert column.validity == TERRAIN_CURRENT
    assert column.surface_row == 9
    assert column.surface_address == _block_addr(112, 9)
    assert column.surface_value == 0x66
    assert (_block_addr(112, 9), 0x66) in column.collision_samples


def test_radar_ignores_defeated_enemy_slots():
    ram = bytearray(0x800)
    player_x = 256
    ram[ADDR_SCREEN_RIGHT_PAGE] = 0x01
    ram[ADDR_SCREEN_RIGHT_X] = 0xF0
    _ground(ram, player_x, row=11)
    for world_x in (272, 288, 304, 320):
        _ground(ram, world_x, row=11)

    ram[ADDR_ENEMY_FLAG] = 1
    ram[ADDR_ENEMY_ID] = 0x06
    ram[ADDR_ENEMY_STATE] = 0x20
    ram[ADDR_ENEMY_PAGE] = 0x01
    ram[ADDR_ENEMY_X] = 0x30
    ram[ADDR_ENEMY_Y_HIGH] = 1
    ram[ADDR_ENEMY_Y] = 176

    radar = decode_smb1_radar(bytes(ram), player_x=player_x, lookahead_px=64)

    assert radar.nearest_enemy_dx is None
    assert radar.enemies == ()


def test_power_up_object_is_reward_not_enemy_hazard():
    ram = bytearray(0x800)
    player_x = 0x0600
    ram[ADDR_SCREEN_RIGHT_PAGE] = 0x06
    ram[ADDR_SCREEN_RIGHT_X] = 0xD0
    ram[ADDR_POWER_UP_TYPE] = 2  # Star
    ram[ADDR_PLAYER_STATUS] = 0
    ram[ADDR_STAR_INVINCIBLE_TIMER] = 0

    slot = POWER_UP_SLOT
    ram[ADDR_ENEMY_FLAG + slot] = 1
    ram[ADDR_ENEMY_ID + slot] = POWER_UP_OBJECT_ID
    ram[ADDR_ENEMY_STATE + slot] = 0x80
    ram[ADDR_ENEMY_PAGE + slot] = 0x06
    ram[ADDR_ENEMY_X + slot] = 0x40
    ram[ADDR_ENEMY_Y_HIGH + slot] = 1
    ram[ADDR_ENEMY_Y + slot] = 144

    radar = decode_smb1_radar(bytes(ram), player_x=player_x, lookahead_px=192)
    payload = radar.to_payload()

    assert radar.enemies == ()
    assert radar.nearest_enemy_dx is None
    assert radar.nearest_reward_dx == 64
    assert radar.nearest_reward_type == "star"
    assert radar.rewards[0].slot == POWER_UP_SLOT
    assert radar.rewards[0].power_up_type == 2
    assert payload["rewards"][0]["type"] == "star"


def test_radar_exposes_player_capability_state():
    ram = bytearray(0x800)
    player_x = 100
    ram[ADDR_SCREEN_RIGHT_PAGE] = 0
    ram[ADDR_SCREEN_RIGHT_X] = 220
    ram[ADDR_PLAYER_STATUS] = 2
    ram[ADDR_STAR_INVINCIBLE_TIMER] = 0x23

    radar = decode_smb1_radar(bytes(ram), player_x=player_x, lookahead_px=64)

    assert radar.player_status == 2
    assert radar.star_invincible_timer == 0x23
    assert radar.invincible is True
    assert radar.to_payload()["invincible"] is True
