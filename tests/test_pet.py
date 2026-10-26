import hashlib
import json
import math
from datetime import datetime

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest

from ameath.config import Config
from ameath.pet import DesktopPet, clamp_position, direction_for_vector
from ameath.sprites import CELL_HEIGHT, CELL_WIDTH, CLIPS, FacingAnimator, SpriteAnimator, SpriteError, SpriteSheet, load_snowcap
from test_ui import app


@pytest.fixture
def atlas():
    image = QImage(1536, 1872, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    for clip in CLIPS.values():
        for column in range(clip.frames):
            painter.fillRect(column * CELL_WIDTH + 10, clip.row * CELL_HEIGHT + 10, 12, 12,
                             QColor(30 + clip.row * 20, 30 + column * 25, 140, 255))
    painter.end()
    return image


def test_atlas_crops_only_required_frames_and_keeps_transparency(atlas):
    sheet = SpriteSheet(atlas)
    assert sheet.version == 1
    for name, clip in CLIPS.items():
        for index in range(clip.frames):
            frame = sheet.frame(name, index)
            assert frame.size() == QSize(192, 208)
            assert frame.pixelColor(12, 12) == QColor(30 + clip.row * 20, 30 + index * 25, 140, 255)
            assert frame.pixelColor(0, 0).alpha() == 0
        assert sheet.frame(name, clip.frames) == sheet.frame(name, 0)


@pytest.mark.parametrize("missing", [True, False])
def test_malformed_atlas_is_rejected_before_it_can_flash_empty_or_adjacent_cells(atlas, missing):
    painter = QPainter(atlas)
    if missing:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(0, 0, CELL_WIDTH, CELL_HEIGHT, Qt.GlobalColor.transparent)
    else:
        painter.fillRect(7 * CELL_WIDTH, 0, 10, 10, Qt.GlobalColor.white)
    painter.end()
    with pytest.raises(SpriteError):
        SpriteSheet(atlas)


def test_resource_is_copied_unchanged_and_cached_independently_of_codex(tmp_path, atlas):
    source = tmp_path / "original"
    source.mkdir()
    (source / "pet.json").write_text(json.dumps({"spritesheetPath": "spritesheet.webp"}))
    original = source / "spritesheet.webp"
    assert atlas.save(str(original), "WEBP")
    before = original.read_bytes()
    sheet, warning = load_snowcap(tmp_path / "ameath", source)
    assert not warning and sheet is not None
    assert original.read_bytes() == before == sheet.source.read_bytes()
    metadata = json.loads(sheet.source.with_name("source.json").read_text())
    assert metadata["sha256"] == hashlib.sha256(before).hexdigest()
    original.unlink()
    cached, warning = load_snowcap(tmp_path / "ameath", source)
    assert not warning and cached is not None
    assert cached.frame("waving", 2) == sheet.frame("waving", 2)


def test_missing_resource_has_a_usable_fallback(tmp_path):
    sheet, warning = load_snowcap(tmp_path / "data", tmp_path / "missing")
    assert sheet is None and "简易外观" in warning


def test_animation_uses_elapsed_time_and_wraps_without_entering_unused_cells():
    animator = SpriteAnimator()
    animator.advance(159)
    assert animator.frame_index == 0
    animator.advance(1)
    assert animator.frame_index == 1
    animator.advance(160 * 5)
    assert animator.frame_index == 0
    animator.set_motion("left")
    animator.advance(85 * 7)
    assert animator.action == "running-left" and animator.frame_index == 7
    animator.advance(85)
    assert animator.frame_index == 0


def test_reaction_returns_to_task_and_movement_has_priority():
    animator = SpriteAnimator()
    animator.set_base("running")
    animator.play("waving")
    animator.advance(160)
    animator.set_motion("left")
    animator.advance(2000)
    assert animator.action == "running-left"
    animator.set_base("waiting")
    animator.set_motion(None)
    assert animator.action == "waving" and animator.frame_index == 0
    animator.advance(4 * 160 + 180)
    assert animator.action == "waiting" and animator.frame_index == 1


@pytest.fixture
def pet(app):
    widget = DesktopPet(time_provider=lambda: datetime(2026, 10, 25, 12))
    available = app.primaryScreen().availableGeometry()
    widget.move(available.topLeft() + QPoint(20, 20))
    widget.show()
    app.processEvents()
    widget.timer.stop()
    widget.stop_moving()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def mouse(pet, kind, position):
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    event = QMouseEvent(kind, QPointF(position - pet.pos()), QPointF(position), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseButtonPress: pet.mousePressEvent,
     QEvent.Type.MouseMove: pet.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: pet.mouseReleaseEvent}[kind](event)


def test_walking_reaches_its_midpoint_and_stops_on_target(pet):
    start = pet.pos()
    pet.move_to(start + QPoint(120, 0), speed=120)
    assert pet.animation == "running-right"
    pet.advance(500)
    assert pet.pos() == start + QPoint(60, 0)
    pet.advance(500)
    assert pet.pos() == start + QPoint(120, 0)
    assert not pet.is_moving and pet.animation == "idle"
    pet.move_to(start, speed=120)
    assert pet.animation == "running-left"
    pet.say("正在思考", "thinking")
    assert not pet.is_moving and pet.animation == "running"


def test_drag_changes_direction_then_lands_and_resumes_the_task_without_click(pet):
    activated = []
    pet.activated.connect(lambda: activated.append(True))
    pet.set_activity("thinking")
    start = pet.pos()
    press = start + QPoint(100, 150)
    mouse(pet, QEvent.Type.MouseButtonPress, press)
    mouse(pet, QEvent.Type.MouseMove, press + QPoint(120, 0))
    assert pet.pos() == start + QPoint(120, 0)
    assert pet.animation == "running-right"
    mouse(pet, QEvent.Type.MouseMove, press + QPoint(80, 0))
    assert pet.animation == "running-left"
    pet.say("任务仍在进行", "thinking")
    assert pet.animation == "running-left"
    pet.advance(181)
    assert pet.animation == "running"
    mouse(pet, QEvent.Type.MouseButtonRelease, press + QPoint(80, 0))
    assert pet.pos() == start + QPoint(80, 0)
    assert pet.animation == "jumping" and not activated
    pet.advance(550)
    assert pet.animation == "running"


def test_click_waves_and_activates_but_distant_release_clamps_without_activation(pet):
    activated = []
    pet.activated.connect(lambda: activated.append(True))
    press = pet.pos() + QPoint(100, 150)
    mouse(pet, QEvent.Type.MouseButtonPress, press)
    mouse(pet, QEvent.Type.MouseButtonRelease, press)
    assert activated == [True] and pet.animation == "waving"
    mouse(pet, QEvent.Type.MouseButtonPress, press)
    # Native systems can omit a move event immediately before release.
    mouse(pet, QEvent.Type.MouseButtonRelease, QPoint(-10000, -10000))
    available = pet.screen().availableGeometry()
    assert pet.pos() == available.topLeft()
    assert activated == [True] and pet.animation == "jumping"


def test_wandering_waits_for_reaction_and_stays_still_when_busy_or_paused(pet):
    pet.set_wandering(True)
    pet.say("完成了", "happy")
    pet._wander_in_ms = 0
    pet.advance(639)
    assert not pet.is_moving
    pet.advance(1)
    assert pet.is_moving
    for state in ("thinking", "waiting", "review", "paused"):
        pet.set_activity(state)
        pet._wander_in_ms = 0
        pet.advance(15000)
        assert not pet.is_moving
    pet.set_activity("idle")
    pet._wander_in_ms = 0
    pet.advance(1)
    assert pet.is_moving
    pet.set_wandering(False)
    assert not pet.is_moving


def test_hidden_pet_stops_timer_and_releases_motion(pet, app):
    pet.hide()
    assert not pet.timer.isActive()
    pet.show()
    app.processEvents()
    assert pet.timer.isActive()
    pet.move_to(pet.pos() + QPoint(100, 0))
    pet.hide()
    assert not pet.timer.isActive() and not pet.is_moving


def test_visible_pet_advances_frames_from_the_real_qt_timer(pet):
    pet.set_activity("idle")
    pet._clock.restart()
    pet.timer.start()
    QTest.qWait(220)
    assert pet.frame_index >= 1
    pet.timer.stop()


def test_chat_reply_defers_its_expression_until_dragging_stops(pet):
    press = pet.pos() + QPoint(100, 150)
    mouse(pet, QEvent.Type.MouseButtonPress, press)
    mouse(pet, QEvent.Type.MouseMove, press + QPoint(80, 0))
    pet.respond("太好了，值得庆祝！", "jumping")
    assert pet.animation == "running-right"
    pet.advance(181)
    assert pet.animation == "jumping"
    mouse(pet, QEvent.Type.MouseButtonRelease, press + QPoint(80, 0))
    pet.advance(550)
    assert pet.animation == "idle"


def test_clamp_supports_negative_monitor_coordinates_and_small_work_areas():
    screen = QRect(-1920, -100, 1920, 1080)
    assert clamp_position(QPoint(-5000, -5000), screen, QSize(248, 316)) == QPoint(-1920, -100)
    assert clamp_position(QPoint(100, 2000), screen, QSize(248, 316)) == QPoint(-248, 664)
    assert clamp_position(QPoint(200, 200), QRect(10, 20, 100, 100), QSize(248, 316)) == QPoint(10, 20)


def test_wandering_preference_survives_restart(tmp_path):
    config = Config(tmp_path)
    assert not config.settings.pet_wandering
    config.settings.pet_wandering = True
    config.save()
    assert Config(tmp_path).settings.pet_wandering


def test_idle_is_bubble_free_and_messages_fade_without_moving_the_pet(pet):
    position = pet.pos()
    assert not pet.bubble_visible
    assert pet.grab().toImage().pixelColor(20, 20).alpha() == 0
    pet.say("你好")
    assert pet.bubble_visible
    pet.advance(120)
    assert pet.bubble_opacity == 1
    pet.advance(3280)
    assert 0 < pet.bubble_opacity < 1
    pet.advance(120)
    assert not pet.bubble_visible and pet.pos() == position
    assert pet.grab().toImage().pixelColor(20, 20).alpha() == 0


@pytest.mark.parametrize("vector,direction", [((100, 0), "right"), ((-100, 0), "left"),
    ((0, -100), "up"), ((0, 100), "down"), ((100, -100), "up-right"),
    ((-100, -100), "up-left"), ((100, 100), "down-right"), ((-100, 100), "down-left")])
def test_all_eight_movement_directions(vector, direction):
    assert direction_for_vector(*vector) == direction


def test_direction_hysteresis_prevents_jitter_near_a_sector_boundary():
    assert direction_for_vector(100, 50, "right") == "right"
    assert direction_for_vector(100, 70, "right") == "down-right"


def test_vertical_and_diagonal_paths_have_gentle_acceleration_and_reach_the_target(pet):
    start = pet.pos()
    pet.move_to(start + QPoint(0, 120), speed=120)
    assert pet.animation == "running-down"
    pet.advance(100)
    assert 0 < pet.y() - start.y() < 12
    pet.advance(900)
    assert pet.pos() == start + QPoint(0, 120)
    pet.move_to(start + QPoint(120, 0), speed=120)
    assert pet.animation == "running-up-right"
    pet.advance(1500)
    assert pet.pos() == start + QPoint(120, 0) and not pet.is_moving


def test_autonomous_walking_changes_both_coordinates(pet, app, monkeypatch):
    pet.move(app.primaryScreen().availableGeometry().center() - pet.rect().center())
    pet.stop_moving()
    start = pet.pos()
    values = iter([-math.pi / 4, 160, 10000])
    monkeypatch.setattr("ameath.pet.random.uniform", lambda low, high: next(values))
    pet.walk_once()
    assert pet.animation == "running-up-right"
    pet.advance(4000)
    assert pet.x() > start.x() and pet.y() < start.y()


def test_turning_preserves_gait_phase_and_standing_views_use_the_shortest_arc():
    animator = SpriteAnimator()
    animator.set_motion("right")
    animator.advance(340)
    animator.set_motion("up")
    assert animator.frame_index == 4
    animator.set_motion("left")
    assert animator.frame_index == 4
    facing = FacingAnimator()
    facing.face("left")
    facing.advance(60)
    assert facing.current == 7
    facing.advance(60)
    assert facing.current == 6 and not facing.turning


def test_reaction_queue_finishes_the_current_gesture_instead_of_cutting_it_off():
    animator = SpriteAnimator()
    animator.play("waving")
    animator.advance(320)
    animator.play("jumping", finish_current=True)
    assert animator.action == "waving" and animator.frame_index == 2
    animator.advance(320)
    assert animator.action == "jumping" and animator.frame_index == 0
    animator.advance(550)
    assert animator.action == "idle"


def test_idle_rest_then_sleep_and_click_wakes_without_a_lingering_bubble(pet):
    pet.advance(120000)
    assert pet.state == "sitting" and pet.animation == "sitting-down"
    pet.advance(15000)
    assert pet.state == "idle" and pet.animation == "standing-up"
    pet.advance(165000)
    assert pet.state == "sleeping"
    pet.advance(3000)
    assert pet.animation == "sleeping" and not pet.bubble_visible
    press = pet.pos() + QPoint(100, 150)
    mouse(pet, QEvent.Type.MouseButtonPress, press)
    mouse(pet, QEvent.Type.MouseButtonRelease, press)
    assert pet.state == "idle" and pet.animation == "waking-up"
    pet.advance(960)
    assert pet.animation == "waving"


def test_night_sleep_is_earlier_and_busy_work_prevents_sleep(pet):
    pet._now = lambda: datetime(2026, 10, 25, 23, 30)
    pet.advance(44999)
    assert pet.state == "idle"
    pet.advance(1)
    assert pet.state == "sleeping"
    pet.note_interaction()
    pet.say("正在思考", "thinking")
    pet.advance(600000)
    assert pet.state == "thinking"
    pet.set_activity("idle")
    pet.busy_provider = lambda: True
    pet.advance(600000)
    assert pet.state == "idle"


def test_travel_fatigue_causes_a_short_sitting_break(pet):
    start = pet.pos()
    pet.move_to(start + QPoint(120, 0), speed=20)
    pet.advance(6000)
    assert pet.state == "idle"
    pet.move_to(start, speed=20)
    pet.advance(6000)
    assert pet.state == "sitting" and not pet.is_moving


def test_manual_sitting_sleeping_waking_and_opt_out(pet):
    pet.set_resting(False)
    pet.advance(600000)
    assert pet.state == "idle"
    pet.sit()
    pet.advance(2000)
    assert pet.state == "sitting" and pet.animation == "sitting"
    pet.sleep()
    pet.advance(2000)
    assert pet.state == "sleeping" and pet.animation == "sleeping"
    pet.note_interaction()
    pet.advance(2000)
    assert pet.state == "idle" and pet.animation == "idle"


def test_flight_takes_off_moves_diagonally_and_lands(pet):
    start = pet.pos()
    target = start + QPoint(120, 80)
    pet.fly_to(target, speed=160)
    assert pet.animation == "takeoff" and pet.is_moving and pet.pos() == start
    pet.advance(760)
    assert pet.animation == "flying-right"
    pet.advance(450)
    assert pet.x() > start.x() and pet.y() > start.y()
    pet.advance(500)
    assert pet.animation == "landing" and pet.pos() == target
    pet.advance(1000)
    assert not pet.is_moving and pet.state == "idle" and pet.animation == "idle"


def test_user_can_interrupt_flight_by_dragging(pet):
    pet.fly_to(pet.pos() + QPoint(120, 80))
    pet.advance(800)
    press = pet.pos() + QPoint(100, 150)
    mouse(pet, QEvent.Type.MouseButtonPress, press)
    mouse(pet, QEvent.Type.MouseMove, press + QPoint(-40, -40))
    assert not pet.is_moving and pet.animation == "running-up-left"
    mouse(pet, QEvent.Type.MouseButtonRelease, press + QPoint(-40, -40))


def test_rest_and_flight_preferences_are_persisted(tmp_path):
    config = Config(tmp_path)
    assert config.settings.pet_resting and config.settings.pet_flying
    config.settings.pet_resting = False
    config.settings.pet_flying = False
    config.save()
    restarted = Config(tmp_path)
    assert not restarted.settings.pet_resting and not restarted.settings.pet_flying


def test_generated_rows_with_uneven_gutters_do_not_leak_neighboring_body_parts(tmp_path, atlas):
    image = QImage(800, 480, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    bands = [(15, 130), (155, 220), (245, 350), (385, 465)]
    for row, (top, bottom) in enumerate(bands):
        for column in range(8):
            painter.fillRect(column * 100 + 25, top, 50, bottom - top, QColor(40 + row * 50, 70, 120))
    painter.end()
    path = tmp_path / "uneven.png"
    image.save(str(path))
    sheet = SpriteSheet(atlas)
    sheet.add_generated_atlas(path, ["idle", "waving", "jumping", "turning"], [100] * 4)
    for row, name in enumerate(["idle", "waving", "jumping", "turning"]):
        frame = sheet.frame(name, 0).convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = bytes(frame.constBits())
        reds = {red for red, alpha in zip(pixels[0::4], pixels[3::4]) if alpha > 200}
        assert reds == {40 + row * 50}


def test_right_click_commands_expose_sitting_sleeping_waking_and_flight(pet):
    menu = pet.build_context_menu()
    actions = next(action for action in menu.actions() if action.text() == "动作").menu().actions()
    next(action for action in actions if action.text() == "坐下休息").trigger()
    assert pet.state == "sitting"
    next(action for action in actions if action.text() == "睡一会儿").trigger()
    assert pet.state == "sleeping"
    next(action for action in actions if action.text() == "起床 / 站起来").trigger()
    assert pet.state == "idle"
    next(action for action in actions if action.text() == "飞一圈").trigger()
    assert pet.animation == "takeoff"
    menu.deleteLater()


def test_generated_frames_register_individual_offsets_without_erasing_jump_height(tmp_path, atlas):
    from ameath.sprites import alpha_bounds, body_center_x
    image = QImage(800, 240, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    for column in range(8):
        offset = (-12, 9, -8, 12, 0, -9, 8, 0)[column]
        painter.fillRect(column * 100 + 30 + offset, 25 - column * 2, 40, 180, QColor("#ee88aa"))
    painter.end()
    path = tmp_path / "offsets.png"
    image.save(str(path))
    sheet = SpriteSheet(atlas)
    sheet.add_generated_atlas(path, ["jumping"], [100])
    centers = [body_center_x(sheet.frame("jumping", i)) for i in range(8)]
    assert max(centers) - min(centers) <= 1
    assert alpha_bounds(sheet.frame("jumping", 7)).top() < alpha_bounds(sheet.frame("jumping", 0)).top()


def test_restored_idle_uses_all_original_eight_frames_in_the_original_order(atlas):
    from ameath.config import ROOT
    from ameath.sprites import supplement_snowcap, body_center_x
    reference = SpriteSheet(atlas)
    reference.add_generated_atlas(ROOT / 'assets/snowcap-motion-v2/actions.png',
                                  ['idle', 'waving', 'jumping', 'turning'], [150, 105, 95, 60])
    sheet = supplement_snowcap(SpriteSheet(atlas))
    assert sheet.clips['idle'].frames == 8 and sheet.clips['idle'].frame_ms == 150
    for i in range(8):
        assert sheet.frame('idle', i) == reference.frame('idle', i)
    assert len({bytes(sheet.frame('idle', i).constBits()) for i in range(8)}) == 8
    centers = [body_center_x(sheet.frame('idle', i)) for i in range(8)]
    assert max(centers) - min(centers) <= 1


def test_restored_pet_paints_the_whole_original_frame_without_deformation(pet, atlas):
    from ameath.sprites import supplement_snowcap
    pet.sheet = supplement_snowcap(SpriteSheet(atlas))
    pet.animator = SpriteAnimator(pet.sheet.clips)
    for action in ('idle', 'waving'):
        pet.animator.set_base(action, clear_reaction=True)
        clip = pet.sheet.clips[action]
        for index in range(clip.frames):
            pet.animator.elapsed_ms = index * clip.frame_ms
            actual = QImage(192, 208, QImage.Format.Format_ARGB32_Premultiplied)
            actual.fill(Qt.GlobalColor.transparent)
            painter = QPainter(actual)
            pet.render(painter, QPoint(-28, -82))
            painter.end()
            assert actual == pet.sheet.frame(action, index)
