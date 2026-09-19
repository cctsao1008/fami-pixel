import pytest

from fami_pixel.games.smb1.state import (
    ADDR_FRAME_COUNTER,
    ADDR_GAME_ENGINE_SUBROUTINE,
    ADDR_LEVEL_NUMBER,
    ADDR_OPER_MODE,
    ADDR_OPER_MODE_TASK,
    ADDR_PLAYER_PAGE,
    ADDR_PLAYER_STATE,
    ADDR_PLAYER_X,
    ADDR_PLAYER_X_SPEED,
    ADDR_PLAYER_Y,
    ADDR_PLAYER_Y_HIGH,
    ADDR_PLAYER_Y_SPEED,
    ADDR_SAVED_JOYPAD1,
    ADDR_WORLD_NUMBER,
    decode_smb1_state,
)


def test_decode_smb1_state_projects_one_coherent_ram_witness():
    ram = bytearray(0x800)
    values = {
        ADDR_FRAME_COUNTER: 0x44,
        ADDR_OPER_MODE: 0x01,
        ADDR_OPER_MODE_TASK: 0x02,
        ADDR_GAME_ENGINE_SUBROUTINE: 0x08,
        ADDR_WORLD_NUMBER: 0x00,
        ADDR_LEVEL_NUMBER: 0x00,
        ADDR_PLAYER_PAGE: 0x06,
        ADDR_PLAYER_X: 0x52,
        ADDR_PLAYER_Y_HIGH: 0x01,
        ADDR_PLAYER_Y: 0xA0,
        ADDR_PLAYER_STATE: 0x02,
        ADDR_PLAYER_X_SPEED: 0x18,
        ADDR_PLAYER_Y_SPEED: 0xF4,
        ADDR_SAVED_JOYPAD1: 0x83,
    }
    for address, value in values.items():
        ram[address] = value

    state = decode_smb1_state(bytes(ram))

    assert state.frame_counter == 0x44
    assert state.game_engine_subroutine == 0x08
    assert state.player_absolute_x == 0x0652
    assert state.player_y == 0xA0
    assert state.player_state == 0x02
    assert state.player_x_speed == 0x18
    assert state.player_y_speed == 0xF4
    assert state.saved_joypad1 == 0x83


def test_decode_smb1_state_rejects_truncated_witness():
    with pytest.raises(ValueError, match="RAM snapshot does not contain address"):
        decode_smb1_state(bytes(32))
