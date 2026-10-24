"""Desktop pointer samples. Pixels are physical; motion/distances use 96-DPI pixels.

Only observe() runs on the input thread. No rendering, picking or OS calls here.
World projection belongs to Unity, never to a guessed mouse depth.
"""
import math
import threading
import uuid


class MouseTargetSampler:
    def __init__(self, smoothing=0.06, moving_speed=8.0, stopped_speed=4.0, timeout=0.25):
        self.smoothing = smoothing
        self.moving_speed = moving_speed
        self.stopped_speed = stopped_speed
        self.timeout = timeout
        self.session = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._sample_lock = threading.Lock()
        self._position = None
        self._source = None
        self._generation = 0
        self._path = 0.0
        self._distance_total = 0.0
        self._motion_at = None
        self._previous = None
        self._velocity = (0.0, 0.0)
        self._moving = False
        self._mapping = None
        self._revision = 0
        self.latest = None

    def observe(self, position, source, now):
        """Constant-space accumulation also preserves an out-and-back stroke."""
        with self._lock:
            if source != self._source:
                self._source = source
                self._generation += 1
                self._motion_at = now
            elif self._position is not None and position != self._position:
                self._path += math.hypot(position[0] - self._position[0], position[1] - self._position[1])
                self._motion_at = now
            self._position = position

    def sample(self, *args, **kwargs):
        # A boundary event and the bridge heartbeat can arrive on different threads.
        with self._sample_lock:
            return self._sample(*args, **kwargs)

    def _sample(self, screen, effective, now, *, window, scale=1.0, display=0,
               source='System', pressed=False, allow_attention=True, interaction_id=0,
               interaction_mode='', bounds=None, geometry_revision=0, position_valid=True,
               forced_look=None):
        self.observe(effective, source, now)
        with self._lock:
            position, path, generation, motion_at = self._position, self._path, self._generation, self._motion_at
        scale = scale if math.isfinite(scale) and scale > 0 else 1.0
        wx, wy, width, height = window
        position_valid = bool(position_valid and width > 0 and height > 0)
        previous = self._previous
        dt = now - previous['timestamp'] if previous else 0.0
        continuous = (previous is not None and 0 < dt <= self.timeout and position_valid
                      and previous['positionValid'] and previous['generation'] == generation
                      and previous['display'] == display and previous['dpiScale'] == scale)
        distance = max(0.0, path - previous['path']) / scale if continuous else 0.0
        self._distance_total += distance
        if continuous:
            raw = tuple((position[i] - previous['position'][i]) / scale / dt for i in (0, 1))
            alpha = 1.0 if self.smoothing <= 0 else -math.expm1(-dt / self.smoothing)
            self._velocity = tuple(a + (b - a) * alpha for a, b in zip(self._velocity, raw))
        else:
            self._velocity = (0.0, 0.0)
            self._moving = False
            motion_at = now
            with self._lock:
                self._motion_at = now
        speed = math.hypot(*self._velocity)
        path_speed = distance / dt if continuous else 0.0
        self._moving = max(speed, path_speed) >= (self.stopped_speed if self._moving else self.moving_speed)
        if not self._moving:
            self._velocity = (0.0, 0.0)
            speed = 0.0
        direction = tuple(v / speed for v in self._velocity) if speed > 0 else (0.0, 0.0)
        mapping = (window, scale, display, geometry_revision)
        if mapping != self._mapping:
            self._mapping = mapping
            self._revision += 1
        valid = position_valid
        uv = ((position[0] - wx) / width, (position[1] - wy) / height) if valid else (0.0, 0.0)
        box = bounds or (0.0, 0.0, 1.0, 1.0)
        x0, y0, x1, y1 = box
        # Same outline-based gaze mapping as the legacy desktop path.
        margin = 0.25 if bounds else 0.2
        near = (x0 - (x1 - x0) * margin <= uv[0] <= x1 + (x1 - x0) * margin
                and y0 - (y1 - y0) * margin <= uv[1] <= y1 + (y1 - y0) * margin)
        eye = ((x0 + x1) / 2, y0 + (y1 - y0) * 0.12) if bounds else (0.5, 0.5)
        half = (max(40.0, (x1 - x0) * width * 0.5), max(60.0, (y1 - y0) * height * 0.6))
        gaze = tuple(max(-1.3, min(1.3, (uv[i] - eye[i]) * (width, height)[i] / half[i]))
                     if near and valid else 0.0 for i in (0, 1))
        if forced_look is not None:
            gaze = tuple(forced_look)
        bx, by = max(x0 - uv[0], 0, uv[0] - x1), max(y0 - uv[1], 0, uv[1] - y1)
        self.latest = {
            'version': 1, 'sessionId': self.session,
            'sequence': (self.latest['sequence'] + 1) if self.latest else 1,
            'timestamp': now, 'positionValid': valid, 'source': source,
            'screenPosition': dict(zip(('x', 'y'), screen)),
            'effectiveScreenPosition': dict(zip(('x', 'y'), position)),
            'windowPosition': dict(zip(('x', 'y'), uv)),
            'windowSize': {'x': width, 'y': height}, 'dpiScale': scale,
            'mappingRevision': self._revision, 'insideViewport': valid and 0 <= uv[0] <= 1 and 0 <= uv[1] <= 1,
            'velocity': dict(zip(('x', 'y'), self._velocity)), 'speed': speed,
            'direction': dict(zip(('x', 'y'), direction)), 'pathDistance': distance,
            'pathTotal': self._distance_total, 'motionSpeed': path_speed,
            'isPressed': bool(pressed), 'isMoving': self._moving,
            'stationaryDuration': 0.0 if self._moving or not valid else max(0.0, now - motion_at),
            'distanceToCharacter': math.hypot(bx * width, by * height) / scale if valid else -1.0,
            'allowAttention': bool(valid and allow_attention and source == 'System'),
            'forcedLook': forced_look is not None,
            'lookPosition': dict(zip(('x', 'y'), gaze)),
            'interactionId': interaction_id, 'interactionMode': interaction_mode,
        }
        self._previous = {'timestamp': now, 'position': position, 'path': path, 'generation': generation,
                          'display': display, 'dpiScale': scale, 'positionValid': position_valid}
        return self.latest
